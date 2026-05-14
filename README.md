# Teslemetry Local Powerwall

Custom Home Assistant integration for the original Powerwall V1 hardware that Tesla no longer exposes through the standard Powerwall integration.

Setup is bootstrapped through your existing [Teslemetry](https://teslemetry.com) integration: pick an energy site, complete a one-time local pairing with the gateway, and the integration then polls the gateway directly over the LAN. No cloud round-trips at runtime.

## Installation (HACS)

1. In HACS, open **Integrations** → menu → **Custom repositories**.
2. Add this repository's URL with category **Integration**.
3. Install **Teslemetry Local Powerwall** and restart Home Assistant.
4. **Settings → Devices & Services → Add Integration → Teslemetry Local Powerwall**, select the Teslemetry entry and energy site, then complete local gateway pairing.

## Manual installation

Copy `custom_components/powerwall_v1r/` into your Home Assistant `config/custom_components/` directory and restart.

## Requirements

- Home Assistant 2024.4 or newer
- The [Teslemetry](https://teslemetry.com) integration installed with at least one energy site
- Network reachability from Home Assistant to the Powerwall gateway

## How it works

Each gateway endpoint is polled on its own cadence by a dedicated coordinator:

- **status** — `/api/system_status` (fast)
- **meters** — `/api/meters/aggregates` (fast)
- **battery SoE** — direct state-of-energy endpoint (medium)
- **grid status** — grid connection state (medium)
- **config** — site configuration (slow; only changes on user edits)

All coordinators share a single authenticated `PowerwallClient`, so the transport and session are reused.

## Entities

The integration creates one device per energy site. Many entities are disabled by default — enable them under the device page if you want the extra detail.

### Power flows (enabled by default)
- Battery / Site / Load / Solar power
- Solar RGM, Generator, Conductor power *(disabled by default)*

### Battery
- Battery state of energy (direct gateway reading, primary)
- Percentage charged (computed from remaining ÷ full pack, diagnostic)
- Energy remaining, Full pack energy

### Per-location meter aggregates *(disabled by default)*
For `site`, `battery`, `load`, `solar`:
- Apparent power, Reactive power, Voltage, Current, Frequency
- Energy imported, Energy exported (long-term statistics)

### Islanding & gateway diagnostics
- Island mode, Islander grid state, Islander grid connection
- Active alerts
- Per-phase ISLANDER frequency & voltage (Load/Main sides) *(mostly disabled by default)*

### SYNC meters X / Y *(disabled by default)*
- Per-CT (A/B/C) real power, reactive power, current
- Per-phase L–N voltages

### Grid & configuration
- Grid status
- Backup reserve percent
- Net meter mode, Customer preferred export rule
- Nominal system energy / power (AC)
- Grid code, Country, Distributor *(disabled by default)*

### Binary sensors
- Grid OK, Site running
- Microgrid OK, Contactor closed, Site manager running *(diagnostic)*
- esCAN / pw3CAN firmware updating *(diagnostic)*

## Releases

This repository uses GitHub Releases. HACS will offer the five most recent releases.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
