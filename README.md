# Powerwall V1R

Custom Home Assistant integration that reconstructs Powerwall-style sensors from a Teslemetry energy site, for installations using the original Powerwall V1 hardware that Tesla no longer exposes through the standard Powerwall integration.

## Installation (HACS)

1. In HACS, open **Integrations** → menu → **Custom repositories**.
2. Add this repository's URL with category **Integration**.
3. Install **Powerwall V1R** and restart Home Assistant.
4. **Settings → Devices & Services → Add Integration → Powerwall V1R** and enter your Teslemetry energy site ID.

## Manual installation

Copy `custom_components/powerwall_v1r/` into your Home Assistant `config/custom_components/` directory and restart.

## Requirements

- Home Assistant 2024.4 or newer
- The [Teslemetry](https://teslemetry.com) integration installed and providing `sensor.energy_site_<id>_*` entities

## Entities

The integration creates a device per configured energy site with the following sensors mirrored from the underlying Teslemetry entities:

- Battery power, percentage charged, energy left, total pack energy
- Solar power
- Load power
- Grid power

## Brand assets

Brand PNGs live in `brand/`. To have the icon shown in Home Assistant's UI globally, also submit them to [home-assistant/brands](https://github.com/home-assistant/brands) under `custom_integrations/powerwall_v1r/`.

## Releases

This repository uses GitHub Releases. HACS will offer the five most recent releases.

## License

MIT
