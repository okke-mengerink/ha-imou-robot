# Imou Vacuum for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/okke-mengerink/ha-imou-robot)](https://github.com/okke-mengerink/ha-imou-robot/releases)
[![Validate workflow status](https://img.shields.io/github/actions/workflow/status/okke-mengerink/ha-imou-robot/validate.yml?branch=main&label=validate)](https://github.com/okke-mengerink/ha-imou-robot/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/github/license/okke-mengerink/ha-imou-robot)](https://github.com/okke-mengerink/ha-imou-robot/blob/main/LICENSE)
[![Home Assistant 2026.1 or newer](https://img.shields.io/badge/Home%20Assistant-2026.1%2B-18BCF2?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=okke-mengerink&repository=ha-imou-robot&category=integration)

This custom integration, **Imou Vacuum (Open Platform)**, adds your Imou robot vacuum (robot cleaner) to Home Assistant as a standard vacuum entity, with sensors for battery, status, cleaned area and cleaning time. You install it through HACS, set it up in the Home Assistant UI, and it controls the robot through the official Imou Open Platform cloud API (cloud only, with a free developer account). It is tested on the Imou RV-L11-A (L11 series, with the auto-empty dock). Other Imou robots that use the same things-model identifiers may work, but none are verified. Home Assistant's built-in Imou integration and the Imou Life and Imou-OpenPlatform integrations in HACS do not support robot vacuums, which is why this one exists.

## Why this integration?

- **The existing Imou integrations do not cover robot vacuums.** Home Assistant's built-in `imou` integration and the `imou_life` (Imou Life) and `Imou-OpenPlatform` integrations from HACS do not support them.
- **Official API.** It uses the Imou Open Platform cloud API with generic "things model" calls. It is cloud only: there is no local control.
- **A normal vacuum entity.** The robot shows up as a standard Home Assistant `vacuum` entity with sensors, so it works in dashboards, automations and scripts like any other robot vacuum.
- **Model-driven.** Entities are created from the robot's things model at runtime, so models that use the same identifiers can work without code changes.
- **Quota-friendly.** Polling adapts to what the robot is doing. Typical use is about 12,000 API calls per month, against 30,000 included in Imou's free tier.

## Supported devices

| Robot | Status | Notes |
|---|---|---|
| Imou RV-L11-A (L11 series, with auto-empty dock) | Tested | Imou cloud `deviceModel` `L11-A`, Home Assistant 2026.10 |
| Any other Imou robot vacuum | unverified — please share diagnostics | Might work if its things model uses the same identifiers. See [Help us map other models](#help-us-map-other-models). |

Verified on a real RV-L11-A:

- Locate, start, pause, resume and return to dock.
- Suction levels quiet, standard and max (set through the `WindWater` service).
- Carpet boost.
- Battery and status, including "emptying bin" after a run.
- Cleaned area (m²) and cleaning time (min). A first real run of 31 m² in 34 min was tracked correctly.

## Features

- **Vacuum entity:** start (a whole-house clean, or resume when paused), pause, stop, return to dock, locate, and fan speed (the suction level: quiet, standard or max).
- **Sensors:** battery, status, error, cleaned area (m²), cleaning time (min), consumable usage (side brush, main brush, filter, sensors), Wi-Fi signal and a mop-fitted binary sensor.
- **Controls:** suction and water level selects, carpet boost and other switches, and an empty-bin button. See [Entities](#entities).
- **Set up in the UI:** App ID, App Secret and region, with re-authentication when the App Secret changes. English and Dutch translations. A diagnostics download for mapping new models.
- **Automatic discovery:** every robot on your Imou account is found, and only the entities its model supports are created.
- **Adaptive polling:** every 300 s when idle or docked, every 30 s while active, plus a quick refresh after each command.

Not supported yet: rooms and zones, map, schedules. The integration is cloud only; there is no local control.

## Requirements

- Home Assistant 2026.1.0 or newer.
- Cloud only: internet access and the Imou cloud are required. There is no local control.
- The robot must be online (2.4 GHz Wi-Fi) and bound to your Imou account.
- A free Imou Open Platform developer account (see [Setup](#setup)).

## Installation

### HACS

1. In HACS, add a custom repository with category **Integration** and URL `https://github.com/okke-mengerink/ha-imou-robot`, or click the "Open your Home Assistant instance" badge at the top of this page.
2. Install the integration from HACS.
3. Restart Home Assistant.

If HACS reports "Could not download" (a known problem in some Home Assistant/HACS versions with GitHub release downloads), use the manual installation below.

### Manual

1. Copy the `custom_components/imou_vacuum` folder into the `config/custom_components` folder of your Home Assistant installation.
2. Restart Home Assistant.

Then set up the integration as described under [Setup](#setup).

## Setup

1. Create a free developer account on the Imou Open Platform at https://open.imoulife.com and sign in.
2. Open the **Control Board**, go to **Device Access Service** and add your robot with its serial number and safety code (both on the label on the robot), or check that it is already listed.
3. Open **App Information**. It shows your **App ID** and **App Secret**. Keep the App Secret private, like a password.
4. In Home Assistant, go to **Settings > Devices & services > Add integration**, select **Imou Vacuum (Open Platform)**, and enter the App ID and App Secret. Choose the region that matches the data centre of your Imou account: Europe (default), Singapore (Asia) or North America.

If the App Secret changes, Home Assistant offers a re-authentication.

## Renamed in 0.2.0 (was imou_robot)

Up to version 0.1.3 this integration used the domain `imou_robot` and the name "Imou Robot Vacuum". Another community integration uses the same domain, so the two could not be installed side by side, and the names would also clash in the HACS default store. Since 0.2.0 the domain is `imou_vacuum` and the name is **Imou Vacuum (Open Platform)**. The GitHub repository keeps its name.

Home Assistant ties the configuration and the entities to the domain, so upgrading from 0.1.x needs these steps once:

1. In Home Assistant, go to **Settings > Devices & services**, open the old integration and delete it.
2. Delete the folder `custom_components/imou_robot` from your Home Assistant configuration folder.
3. Install 0.2.0 (through HACS, or copy `custom_components/imou_vacuum` as described under [Manual](#manual)) and restart Home Assistant.
4. Go to **Settings > Devices & services > Add integration**, select **Imou Vacuum (Open Platform)** and enter your App ID and App Secret again.

Entity IDs are derived from the device name, so they normally come back the same, and automations and dashboards that use them keep working.

## FAQ

### Does the official Imou integration support robot vacuums?

No. At the time of writing (October 2026), Home Assistant's built-in `imou` integration and the `imou_life` (Imou Life) and `Imou-OpenPlatform` integrations from HACS do not support robot vacuums. This integration adds that support through the official Imou Open Platform cloud API.

### Can I control my Imou robot vacuum locally?

No. This integration is cloud only: every command and status update goes through the Imou cloud, so Home Assistant needs internet access and the robot must be online. Entities become unavailable when the robot stays offline or the cloud cannot be reached for a few polls, see [Failed polls and the robot's status](#failed-polls-and-the-robots-status). There is no local control.

### Is it free?

Yes. The integration is free and open source (MIT license). You need a free Imou Open Platform developer account, which allows 30,000 API calls per month per App ID. Typical use is about 12,000 calls per month, see [Polling and API quota](#polling-and-api-quota). Imou sets the quota and its terms, so check the Open Platform console if you want to be sure about your own account.

### Which region do I choose?

Choose the region that matches the data centre of your Imou account: **Europe** (the default), **Singapore (Asia)** or **North America**. The integration was verified with a Europe account. The other two regions use the same calls on other Imou hosts but are unverified. If setup says it cannot reach the Imou cloud, check the selected region first.

### Can I clean specific rooms?

Not yet. Room and zone cleaning, the map and schedules are not supported yet. The vacuum entity can start a whole-house clean, pause, resume, return to the dock and locate the robot. Until room support exists, you can schedule runs with a Home Assistant automation that calls `vacuum.start`.

### Will it work with Google Home or Alexa?

Not directly: this integration only provides the Home Assistant vacuum entity. Like any Home Assistant vacuum entity, it can be exposed to Google Home through Home Assistant's Google Assistant integration and to Alexa through Home Assistant's Alexa integration (both are available through Home Assistant Cloud or a manual setup). That exposure is set up in Home Assistant, and it has not been verified with this integration.

### Does it work with other Imou robot vacuums?

Maybe. The integration reads each robot's things model (the list of properties and services in the Imou cloud) and creates entities from the identifiers it finds. A device counts as a robot when its things model has the service `clean_control`. Robots that use the same identifiers as the RV-L11-A may work, but only the RV-L11-A is verified. If you have another model, please open a [Help support my Imou robot](https://github.com/okke-mengerink/ha-imou-robot/issues/new?template=new_model.yml) issue and attach the diagnostics file.

### Can I run it next to Home Assistant's built-in Imou integration?

Not with the same App ID. The core `imou` integration polls every 2 minutes and will exhaust the monthly quota of that App ID. See [Polling and API quota](#polling-and-api-quota).

## How it works

The integration controls Imou robot vacuums with generic "things model" calls through the Imou Open Platform cloud API.

- It discovers every robot on your Imou account. A device counts as a robot when its things model has the service `clean_control`.
- Properties and services are resolved at runtime by their identifier in the device's things model. Models that use the same identifiers may work; none other than the RV-L11-A are verified.
- Entities are only created when the identifier exists in the model.
- The integration domain is `imou_vacuum`. In the Home Assistant log it shows up as `custom_components.imou_vacuum`.

## Entities

Entities are created per robot, and only when the robot's model supports them.

### Vacuum

- Controls: start (begins a whole-house clean, or resumes when paused), pause, stop, return to dock, locate, and fan speed (quiet / standard / max, which is the suction level).
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
- Cleaned area (m²)
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

The level maps live in `const.py` (`LEVEL_KEYS`), per identifier. A raw value that is not in the map (0, 4, 99, ...) is shown as `unknown_<n>`, so robots with another scale still show something useful. Levels are set with the `WindWater` service, which always receives both levels: the new one and the current value of the other one. The L11-A accepts a plain `setIotDeviceProperties` write for suction or water (code 0) but ignores it, so property writes are only used for a model that has no `WindWater` service.

### Names and unknown values

Status and error names are translated into English and Dutch. A value the integration does not know is shown as `unknown_<n>`, and the raw model label (in Chinese) is added as the attribute `model_label`.

## Polling and API quota

The integration makes one API call per robot per poll. The poll interval depends on the robot's state:

| State | Poll interval |
|---|---|
| Idle or docked | every 300 s |
| Active: returning, relocating, remote control, room / zone / spot / go-to / whole-house cleaning, emptying bin, return paused | every 30 s |
| After any command | one extra refresh about 3 s after the command, then 30 s polling for 2 minutes |

Pressing start polls the robot once first, so that it can tell "start" from "resume".

Typical use is about 12,000 API calls per month. Imou's free tier allows 30,000 calls per month per App ID.

**Do not** also run Home Assistant's core `imou` integration with the same App ID. It polls every 2 minutes and will exhaust the quota.

### Failed polls and the robot's status

One failed poll does not make the entities unavailable. A failed poll (API error, timeout, network) or a cloud status of `offline` keeps the last good data on screen, and the next poll comes after 60 s instead of 300 s (30 s while the robot is active). The entities become unavailable after 3 failed polls in a row, or when the last good poll is older than 15 minutes. During a longer outage the interval grows to 120 s, 240 s and 300 s, which saves API quota. The statuses `sleep` and `upgrading` are not failures: the last data stays visible.

The log gets one warning at the first failed poll and one info line when polling works again. It never contains your App ID, App Secret or tokens.

## Help us map other models

The integration supports **Download diagnostics** (Settings > Devices & services > Imou Vacuum (Open Platform) > three-dot menu). The file contains the product-level things model and the current values. The App ID and App Secret are redacted, and the device serial and name are not included. Still, open the file and check it before you share it.

If your model is not (fully) working, open a [Help support my Imou robot](https://github.com/okke-mengerink/ha-imou-robot/issues/new?template=new_model.yml) issue, attach the diagnostics file and mention the model name. For anything else that goes wrong, use the [bug report](https://github.com/okke-mengerink/ha-imou-robot/issues/new?template=bug_report.yml) form.

## Contributing

Contributions of identifier and enum mappings are welcome.

## Known limitations and unverified points

- Suction, water level and fan speed are set through the `WindWater` service, verified on an L11-A. Plain property writes for these are ignored by the L11-A. Models without a `WindWater` service get property writes instead (unverified). Other models may use another scale; those values show as `unknown_<n>` and can be added to `LEVEL_KEYS`.
- Property writes work on an L11-A for carpet boost (the cloud confirms the new value) and for return to dock. The L11-A never reports auto-empty or resume after charging, before or after a write, and the Imou app has no such settings, so its firmware probably does not implement them although the shared things model lists them. Whether such a write has any effect cannot be verified.
- A switch whose setting the robot did not report in its first poll is created but disabled by default (the poll must be of an online robot; otherwise nothing is disabled). Enable it in the entity settings if your model supports it. Every switch has the attribute `reported_by_device`. Entities created by an earlier version keep their state: disable or delete those two switches by hand.
- The battery range in the things model is -100 to 100. The value is clamped to 0-100. (A real L11-A reported 41 while charging.)
- Consumable values are assumed to be hours used. A new robot reports 0.
- There is no map, room, zone or schedule control yet.
- Only the Europe region has been verified. Singapore and North America use the same calls on other Imou hosts.
- Commands are fire-and-forget: the cloud output code is not interpreted.
- A value you set from Home Assistant is shown for at most 30 s while the robot still reports the old value; after that the reported value wins, so a command the robot ignores is visible. Only for settings the cloud never reports (auto-empty and resume after charging on the L11-A) the last value you set stays.
- Error information that the robot flags as not valid is ignored (the error sensor shows "none").

## Development

The API client, signing and things-model parsing need no Home Assistant installation:

```bash
python3 -I tests/test_sign.py
python3 -I tests/test_api.py
python3 -I tests/test_model.py
python3 -I tests/test_commanded.py
python3 -I tests/test_health.py
```

The smoke test sets the integration up in a bare Home Assistant core with a fake API client (no network, no credentials):

```bash
uv run --python 3.14 --with "homeassistant==2026.10.2" python -I tests/smoke_ha.py
```

Test fixtures contain an anonymised copy of the product-level L11-A things model. Never commit serial numbers, tokens or App IDs.

See the [changelog](https://github.com/okke-mengerink/ha-imou-robot/blob/main/CHANGELOG.md) for what changed per release.

## Disclaimer

This project is not affiliated with, endorsed by or sponsored by Imou or Dahua. Names are trademarks of their owners. Use at your own risk.

## License

This project is licensed under the MIT License. See the [LICENSE](https://github.com/okke-mengerink/ha-imou-robot/blob/main/LICENSE) file.
