# Imou Robot Vacuum

Home Assistant custom integration for Imou robot vacuums, controlled through the Imou Open Platform cloud API.

- Domain: `imou_robot`
- Version: 0.1.0
- Tested with: RV-L11-A (deviceModel `L11-A`), Home Assistant 2026.10.2
- License: MIT

## Overview

The integration controls Imou robot vacuums with generic "things model" calls through the Imou Open Platform cloud API.

- It discovers every robot on your Imou account. A device counts as a robot when its things model has the service `clean_control`.
- Properties and services are resolved at runtime by their identifier in the device's things model. Models that use the same identifiers should work.
- Entities are only created when the identifier exists in the model.

## Requirements

- Home Assistant 2026.1.0 or newer.
- Cloud only: internet access and the Imou cloud are required. There is no local control.
- The robot must be online (2.4 GHz Wi-Fi) and bound to your Imou account.
- A free Imou Open Platform developer account (see Setup).

## Installation

### HACS

1. In HACS, add a custom repository with category **Integration** and URL `https://github.com/okke-mengerink/ha-imou-robot`.
2. Install the integration from HACS.
3. Restart Home Assistant.

If HACS reports "Could not download" (a known problem in some Home Assistant/HACS versions with GitHub release downloads), use the manual installation below.

### Manual

1. Copy the `custom_components/imou_robot` folder into the `config/custom_components` folder of your Home Assistant installation.
2. Restart Home Assistant.

Then set up the integration as described under Setup.

## Setup

1. Create a free developer account on the Imou Open Platform at https://open.imoulife.com and sign in.
2. Open the **Control Board**, go to **Device Access Service** and add your robot with its serial number and safety code (both on the label on the robot), or check that it is already listed.
3. Open **App Information**. It shows your **App ID** and **App Secret**.
4. In Home Assistant, go to **Settings > Devices & services > Add integration**, select **Imou Robot Vacuum**, and enter the App ID and App Secret. Choose the region that matches the data centre of your Imou account: Europe (default), Singapore (Asia) or North America.

If the App Secret changes, Home Assistant offers a re-authentication.

## Entities

Entities are created per robot, and only when the robot's model supports them.

### Vacuum

- Controls: start (resumes when paused), pause, stop, return to dock, locate, and fan speed (quiet / standard / max, which is the suction level).
- Activity is derived from the robot status:

| Robot status | Vacuum activity |
|---|---|
| standby | idle |
| paused, return-paused | paused |
| returning to dock | returning |
| charging, emptying bin | docked |
| error | error |
| relocating, remote control, room, zone, spot, go-to, whole-house cleaning | cleaning |

- Extra attributes: `work_status` and `error` (raw numbers).

### Sensors

- Battery (%)
- Status (enum)
- Error (enum)
- Cleaned area (m2)
- Cleaning time (min)
- Consumable usage in hours: side brush, main brush, filter and sensors (diagnostic)
- Wi-Fi signal (enum, diagnostic)

### Other entities

- Binary sensor: mop fitted
- Switches: auto-empty, carpet boost, resume after charging
- Selects: suction (quiet / standard / max) and water level (low / medium / high).
- Button: empty bin (empties the bin into the dock)

### Suction and water levels

The Imou app shows suction and water as 3 levels, and the L11-A uses the raw values 1, 2 and 3 for them (its things model declares a misleading range of 0-100):

| Setting | 1 | 2 | 3 |
|---|---|---|---|
| Suction (`WindPower`), also the vacuum fan speed | quiet | standard | max |
| Water (`Water`) | low | medium | high |

The level maps live in `const.py` (`LEVEL_KEYS`), per identifier. A raw value that is not in the map (0, 4, 99, ...) is shown as `unknown_<n>`, so robots with another scale still show something useful. Levels are written with `setIotDeviceProperties`; only if the cloud rejects that write with an API error, the `WindWater` service (which sets both levels together) is tried.

### Names and unknown values

Status and error names are translated into English and Dutch. A value the integration does not know is shown as `unknown_<n>`, and the raw model label (in Chinese) is added as the attribute `model_label`.

## Polling and API quota

The integration makes one API call per robot per poll. The poll interval depends on the robot's state:

| State | Poll interval |
|---|---|
| Idle or docked | every 300 s |
| Active: returning, relocating, remote control, room / zone / spot / go-to / whole-house cleaning, emptying bin, return paused | every 30 s |
| After any command | one extra refresh about 3 s after the command, then 30 s polling for 2 minutes |

Pressing start polls the robot once first, so that it can tell "start" from "resume". After failed polls the interval doubles (30 s, 60 s, ... up to 300 s) until the cloud answers again.

Typical use is about 12,000 API calls per month. Imou's free tier allows 30,000 calls per month per App ID.

**Do not** also run Home Assistant's core `imou` integration with the same App ID. It polls every 2 minutes and will exhaust the quota.

Entities become unavailable when the robot is not online.

## Help us map other models

The integration supports **Download diagnostics** (Settings > Devices & services > Imou Robot Vacuum > three-dot menu). The file contains the product-level things model and the current values. The App ID and App Secret are redacted, and the device serial and name are not included.

If your model is not (fully) working, open an issue in this repository, attach the diagnostics file and mention the model name.

## Contributing

Contributions of identifier and enum mappings are welcome.

## Known limitations and unverified points

- Suction and water levels (1-3) were read correctly on an L11-A; changing them from Home Assistant has not been verified on a real robot yet. Other models may use another scale; those values show as `unknown_<n>` and can be added to `LEVEL_KEYS`.
- The battery range in the things model is -100 to 100. The value is clamped to 0-100. (A real L11-A reported 41 while charging.)
- Consumable values are assumed to be hours used. A new robot reports 0.
- There is no map, room, zone or schedule control yet.
- Commands are fire-and-forget: the cloud output code is not interpreted.
- The cloud does not always report a setting until it was changed once. Until it does, switches show the last value you set from Home Assistant.
- Error information that the robot flags as not valid is ignored (the error sensor shows "none").

## Development

The API client, signing and things-model parsing need no Home Assistant installation:

```bash
python3 -I tests/test_sign.py
python3 -I tests/test_api.py
python3 -I tests/test_model.py
python3 -I tests/test_commanded.py
```

The smoke test sets the integration up in a bare Home Assistant core with a fake API client (no network, no credentials):

```bash
uv run --python 3.14 --with "homeassistant==2026.10.2" python -I tests/smoke_ha.py
```

Test fixtures contain an anonymised copy of the product-level L11-A things model. Never commit serial numbers, tokens or App IDs.

## Disclaimer

This project is not affiliated with, endorsed by or sponsored by Imou or Dahua. Names are trademarks of their owners. Use at your own risk.

## License

This project is licensed under the MIT License.
