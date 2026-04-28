"""Config flow for Powerwall V1R."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from pathlib import Path
from typing import Any

from aiohttp import ClientConnectionError
from tesla_fleet_api.const import (
    AuthorizedClientKeyType,
    AuthorizedClientState,
    AuthorizedClientType,
    Region,
)
from tesla_fleet_api.exceptions import (
    InvalidRegion,
    InvalidToken,
    SubscriptionRequired,
    TeslaFleetError,
)
from tesla_fleet_api.tesla.fleet import TeslaFleetApi
import voluptuous as vol

from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.config_entries import (
    SOURCE_IMPORT,
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigFlowResult,
)
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CLIENT_ID,
    CONF_ENERGY_SITE_ID,
    CONF_GATEWAY_HOST,
    CONF_GATEWAY_PASSWORD,
    CONF_TOKEN,
    DOMAIN,
    KEY_FILENAME,
    KEY_PAIRING_POLL_ATTEMPTS,
    KEY_PAIRING_POLL_INTERVAL,
    LOGGER,
)


def _extract_host(networking_status: dict[str, Any] | None) -> str:
    """Best-effort extraction of an IPv4 from a networking status payload."""
    if not networking_status:
        return ""
    payload = networking_status.get("response", networking_status)
    for path in (
        ("wifi_status", "ip_address"),
        ("wifi_status", "ipv4_address"),
        ("ethernet_status", "ip_address"),
        ("ethernet_status", "ipv4_address"),
    ):
        node: Any = payload
        for key in path:
            if not isinstance(node, Mapping):
                node = None
                break
            node = node.get(key)
        if isinstance(node, str) and node:
            return node
    return ""


def _is_verified_for_key(
    list_response: dict[str, Any], public_key_b64: str
) -> bool:
    """Return True if list_authorized_clients shows our key as VERIFIED."""
    payload = list_response.get("response", list_response)
    clients: Any = payload.get("authorized_clients") if isinstance(payload, Mapping) else None
    if not isinstance(clients, list):
        # Some firmwares wrap the list a layer deeper.
        for value in (payload or {}).values() if isinstance(payload, Mapping) else ():
            if isinstance(value, list):
                clients = value
                break
    if not isinstance(clients, list):
        return False
    for client in clients:
        if not isinstance(client, Mapping):
            continue
        key = client.get("public_key") or client.get("publicKey")
        if not key or key != public_key_b64:
            continue
        state = client.get("state") or client.get("authorized_client_state")
        if state in (
            AuthorizedClientState.VERIFIED,
            int(AuthorizedClientState.VERIFIED),
            "VERIFIED",
            "AUTHORIZED_CLIENT_STATE_VERIFIED",
        ):
            return True
    return False


class OAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow to handle Powerwall V1R OAuth2 authentication and pairing."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow state."""
        super().__init__()
        self._token_data: dict[str, Any] = {}
        self._fleet: TeslaFleetApi | None = None
        self._key_pem: bytes | None = None
        self._sites_remaining: list[dict[str, Any]] = []
        self._pending: list[dict[str, Any]] = []
        self._pair_attempt = 0

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return LOGGER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Auto-import the client credential, then start OAuth."""
        if CLIENT_ID:
            await async_import_client_credential(
                self.hass,
                DOMAIN,
                ClientCredential(CLIENT_ID, "", name="Powerwall V1R"),
            )
        return await super().async_step_user(user_input)

    async def async_oauth_create_entry(
        self, data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Discover energy sites and start the per-site flow."""
        self._token_data = data
        access_token = data["token"]["access_token"]
        session = async_get_clientsession(self.hass)

        fleet = TeslaFleetApi(
            session=session,
            access_token=access_token,
            region=Region.NA,
            charging_scope=False,
            partner_scope=False,
            user_scope=False,
            vehicle_scope=False,
        )
        self._fleet = fleet

        try:
            response = await fleet.products()
        except InvalidRegion:
            try:
                await fleet.find_server()
                response = await fleet.products()
            except InvalidToken:
                return self.async_abort(reason="oauth_error")
            except SubscriptionRequired:
                return self.async_abort(reason="subscription_required")
            except ClientConnectionError:
                return self.async_abort(reason="cannot_connect")
            except TeslaFleetError as err:
                LOGGER.error("Fleet API error: %s", err)
                return self.async_abort(reason="unknown")
        except InvalidToken:
            return self.async_abort(reason="oauth_error")
        except SubscriptionRequired:
            return self.async_abort(reason="subscription_required")
        except ClientConnectionError:
            return self.async_abort(reason="cannot_connect")
        except TeslaFleetError as err:
            LOGGER.error("Fleet API error: %s", err)
            return self.async_abort(reason="unknown")

        products = response.get("response", []) or []
        sites: list[dict[str, Any]] = []
        for product in products:
            site_id = product.get("energy_site_id")
            if not site_id:
                continue
            site_name = (
                product.get("site_name") or f"Energy Site {site_id}"
            )
            api_site = fleet.energySites.create(int(site_id))
            host = ""
            try:
                networking = await api_site.get_networking_status()
                host = _extract_host(networking)
            except TeslaFleetError as err:
                LOGGER.debug(
                    "Networking status unavailable for site %s: %s",
                    site_id,
                    err,
                )
            sites.append(
                {
                    "site_id": int(site_id),
                    "site_name": site_name,
                    "host": host,
                }
            )

        if not sites:
            return self.async_abort(reason="no_sites")

        # Reauth/Reconfigure: just refresh the token on the existing entry.
        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            new_data = {**entry.data, CONF_TOKEN: data}
            return self.async_update_reload_and_abort(entry, data=new_data)
        if self.source == SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            new_data = {**entry.data, CONF_TOKEN: data}
            return self.async_update_reload_and_abort(entry, data=new_data)

        self._sites_remaining = sites
        return await self.async_step_password()

    async def async_step_password(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for the gateway password for each discovered site."""
        if not self._sites_remaining:
            if not self._pending:
                return self.async_abort(reason="no_sites")
            return await self.async_step_pair_install()

        site = self._sites_remaining[0]

        if user_input is not None:
            password = (user_input.get(CONF_GATEWAY_PASSWORD) or "").strip()
            host = (user_input.get(CONF_GATEWAY_HOST) or site["host"]).strip()
            self._sites_remaining.pop(0)
            if password:
                self._pending.append(
                    {
                        "site_id": site["site_id"],
                        "site_name": site["site_name"],
                        "host": host,
                        "password": password,
                        "paired": False,
                        "lan_ok": False,
                    }
                )
            return await self.async_step_password()

        schema = vol.Schema(
            {
                vol.Optional(CONF_GATEWAY_PASSWORD, default=""): str,
                vol.Optional(CONF_GATEWAY_HOST, default=site["host"]): str,
            }
        )
        return self.async_show_form(
            step_id="password",
            data_schema=schema,
            description_placeholders={"site_name": site["site_name"]},
        )

    async def async_step_pair_install(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Install the RSA public key on each pending site."""
        assert self._fleet is not None

        if self._key_pem is None:
            try:
                await self._fleet.get_rsa_private_key(
                    self.hass.config.path(KEY_FILENAME)
                )
            except OSError as err:
                LOGGER.error("Could not read/write RSA key: %s", err)
                return self.async_abort(reason="unknown")
            try:
                self._key_pem = await self.hass.async_add_executor_job(
                    Path(self.hass.config.path(KEY_FILENAME)).read_bytes
                )
            except OSError as err:
                LOGGER.error("Could not read RSA key file: %s", err)
                return self.async_abort(reason="unknown")

            for site in self._pending:
                api_site = self._fleet.energySites.create(site["site_id"])
                try:
                    await api_site.add_authorized_client(
                        self._fleet.rsa_public_der_pkcs1,
                        description="Powerwall V1R",
                        key_type=AuthorizedClientKeyType.RSA,
                        authorized_client_type=AuthorizedClientType.CUSTOMER_MOBILE_APP,
                    )
                except TeslaFleetError as err:
                    LOGGER.error(
                        "add_authorized_client failed for site %s: %s",
                        site["site_id"],
                        err,
                    )
                    return self.async_abort(reason="pair_install_failed")

        if user_input is None:
            return self.async_show_form(
                step_id="pair_install",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "attempt": str(self._pair_attempt + 1)
                },
            )

        return await self.async_step_pair_verify()

    async def async_step_pair_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Poll list_authorized_clients until each pending site is VERIFIED."""
        assert self._fleet is not None

        public_key_b64 = self._fleet.rsa_public_der_pkcs1_b64

        for _ in range(KEY_PAIRING_POLL_ATTEMPTS):
            all_verified = True
            for site in self._pending:
                if site["paired"]:
                    continue
                api_site = self._fleet.energySites.create(site["site_id"])
                try:
                    response = await api_site.list_authorized_clients()
                except TeslaFleetError as err:
                    LOGGER.debug(
                        "list_authorized_clients error for site %s: %s",
                        site["site_id"],
                        err,
                    )
                    all_verified = False
                    continue
                if _is_verified_for_key(response, public_key_b64):
                    site["paired"] = True
                else:
                    all_verified = False
            if all_verified:
                return await self.async_step_lan_verify()
            await asyncio.sleep(KEY_PAIRING_POLL_INTERVAL)

        # Did not verify in this batch — let the user retry.
        self._pair_attempt += 1
        return self.async_show_form(
            step_id="pair_install",
            data_schema=vol.Schema({}),
            errors={"base": "pair_pending"},
            description_placeholders={"attempt": str(self._pair_attempt + 1)},
        )

    async def async_step_lan_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify LAN connectivity to each paired site, then create entries."""
        # Find the next site that hasn't been LAN-verified yet.
        site = next((s for s in self._pending if not s["lan_ok"]), None)
        if site is None:
            return await self._async_finish_entries()

        errors: dict[str, str] = {}
        if user_input is not None:
            site["password"] = user_input.get(
                CONF_GATEWAY_PASSWORD, site["password"]
            )
            site["host"] = user_input.get(CONF_GATEWAY_HOST, site["host"])

        from aiopowerwall import (  # noqa: PLC0415
            PowerwallAuthenticationError,
            PowerwallClient,
            PowerwallConnectionError,
        )

        attempted = user_input is not None or site.get("auto_attempted") is None
        site["auto_attempted"] = True

        if attempted and site["host"] and self._key_pem is not None:
            session = async_get_clientsession(self.hass)
            try:
                async with PowerwallClient(
                    host=site["host"],
                    gateway_password=site["password"],
                    rsa_private_key_pem=self._key_pem,
                    session=session,
                ) as client:
                    await client.connect()
                site["lan_ok"] = True
                return await self.async_step_lan_verify()
            except PowerwallAuthenticationError:
                errors["base"] = "invalid_password"
            except PowerwallConnectionError:
                errors["base"] = "cannot_connect_local"
            except Exception as err:  # noqa: BLE001
                LOGGER.exception("Unexpected LAN verify error: %s", err)
                errors["base"] = "unknown"

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_GATEWAY_HOST, default=site["host"]
                ): str,
                vol.Required(
                    CONF_GATEWAY_PASSWORD, default=site["password"]
                ): str,
            }
        )
        return self.async_show_form(
            step_id="lan_verify",
            data_schema=schema,
            errors=errors,
            description_placeholders={"site_name": site["site_name"]},
        )

    async def _async_finish_entries(self) -> ConfigFlowResult:
        """Create one config entry per verified site."""
        verified = [s for s in self._pending if s["lan_ok"]]
        if not verified:
            return self.async_abort(reason="no_sites")

        first, *rest = verified
        for site in rest:
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": SOURCE_IMPORT},
                    data=self._build_entry_data(site),
                )
            )

        await self.async_set_unique_id(str(first["site_id"]))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=first["site_name"],
            data=self._build_entry_data(first),
        )

    def _build_entry_data(self, site: dict[str, Any]) -> dict[str, Any]:
        """Build the persisted data for a single site's config entry."""
        return {
            CONF_TOKEN: self._token_data,
            CONF_ENERGY_SITE_ID: site["site_id"],
            CONF_GATEWAY_HOST: site["host"],
            CONF_GATEWAY_PASSWORD: site["password"],
            "site_name": site["site_name"],
        }

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Create a config entry from data dispatched by the parent flow."""
        site_id = import_data[CONF_ENERGY_SITE_ID]
        await self.async_set_unique_id(str(site_id))
        self._abort_if_unique_id_configured()
        title = import_data.get("site_name") or f"Energy Site {site_id}"
        return self.async_create_entry(title=title, data=import_data)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth on token failure."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth dialog."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                description_placeholders={"name": "Powerwall V1R"},
            )
        return await super().async_step_user()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration."""
        return await self.async_step_user()
