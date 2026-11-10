# Changelog

## 0.3.0

- New: room cleaning, with Home Assistant 2026.3 or newer. The robot's rooms appear as segments that you map to Home Assistant areas; `vacuum.clean_area` (or Assist) then cleans them. Verified on an RV-L11-A: `clean_control` in room mode with `partition_area` as a JSON list of room ids, for one room and for two rooms in one command.
- The Imou Open Platform API does not report the map or the room list. On an L11-A the properties stay empty, even while the robot cleans a room. The rooms are therefore read from the robot's schedules (`clean_list`). Name the rooms in the integration options (Configure), one `id = name` per line. Names from the app are room types and are only used when the pairing is certain.
- New: a Schedules sensor with the number of enabled schedules and the details as an attribute. The schedules are read once an hour (about 720 extra API calls per month).
- New: `vacuum.send_command` calls any service of the robot's things model. Unknown services and inputs are rejected; values are sent unchanged.
- Diagnostics: a read-only probe of the room, map and schedule data (names, tokens, network details and the path and query of URLs are hidden; only public https URLs are checked, without following redirects) and the last command with its result.
- Opening the room mapping or the options reads the schedules at most once a minute. Saving the options reloads the integration only when the options really changed.
- Home Assistant 2026.1 and 2026.2 keep working as before; room cleaning is only enabled where Home Assistant supports it.

## 0.2.1

- Docs: the README asks users to star the repository and to share diagnostics of other Imou robots, with a new "Support the project" section.
- No code or behaviour changes.

## 0.2.0

**Breaking: the integration domain is now `imou_vacuum` (was `imou_robot`), and the name is "Imou Vacuum (Open Platform)" (was "Imou Robot Vacuum").** Another community integration uses the same domain and name, so the two could not be installed together, and the names would clash in the HACS default store. There is no automatic migration: Home Assistant ties the configuration and the entities to the domain.

- Breaking: the folder is now `custom_components/imou_vacuum`, and the logger is `custom_components.imou_vacuum`.
- Upgrading from 0.1.x: delete the old integration in Home Assistant (Settings > Devices & services), delete the folder `custom_components/imou_robot`, install 0.2.0, restart Home Assistant, and add "Imou Vacuum (Open Platform)" again with your App ID and App Secret. Entity IDs are derived from the device name, so they normally come back the same.
- The display name is changed in `manifest.json`, `hacs.json`, the config flow title and the translations.
- Fixed: the consumable sensors (side brush, main brush, filter, sensors) treat the robot's value as minutes used and show hours (minutes / 60, one decimal). The L11-A reports minutes although its things model says hours: after a 34-minute run all four read 34 h. They are also recorded as statistics now (state class measurement).
- No other behaviour changes. The GitHub repository keeps its name.

## 0.1.3

- Fix: entities no longer go `unavailable` after a single failed cloud poll. On the first real cleaning run they dropped out twice (1 minute while cleaning, 5 minutes while docked). A failed `getIotDeviceProperties` poll (API error, timeout, network) now keeps the last good data. The entities become unavailable only after 3 failed polls in a row, or when the last good poll is older than 15 minutes. The next poll after a failure comes after 60 s instead of 300 s (30 s while the robot is active), and a long outage backs off to the idle interval to save API quota.
- Fix: the cloud statuses `sleep` and `upgrading` are no longer treated as offline: the last data stays visible. Only `offline` counts as a failure, with the same rule (3 polls or 15 minutes).
- Logging: one warning at the first failed poll and one info line when polling works again, no repeated lines and no secrets. The diagnostics download shows the number of failed polls in a row.
- Docs: README rewritten for discoverability: new summary, badges, "Why this integration?", supported devices table, features list and FAQ. The behaviour verified on a real RV-L11-A is listed, including a first real run of 31 m² in 34 min.
- Docs: GitHub issue forms for bug reports and "Help support my Imou robot" requests (model name and diagnostics).
- Docs: the README no longer hard-codes the version; the release badge shows it.

## 0.1.2

- Switches whose setting the robot did not report in its first online poll are created disabled by default. The L11-A never reports auto-empty (11800) or resume after charging (11700), before or after a write, and the Imou app has no such settings. Carpet boost (11900) is reported, works both ways and stays enabled. Enable a disabled switch in the entity settings if your model supports it. Entities created by an earlier version keep their state; disable or delete them by hand.
- New attribute `reported_by_device` on every switch, and the diagnostics download lists which settings the robot reports.
- Docs: known limitations updated.

## 0.1.1

- Fix: suction, water level and fan speed are now set through the `WindWater` service, which always receives both levels (the other one from the robot's latest reported value). On the L11-A a plain property write for these is accepted (code 0) but ignored by the robot. Models without a `WindWater` service still get property writes. The "retry through the service after an API error" logic is gone.
- Fix: a value you set from Home Assistant is shown for at most 30 s while the robot still reports a different value; after that the reported value wins, so a command that the robot ignores is visible. Settings the cloud never reports (auto-empty, resume after charging on the L11-A) keep the last value you set.
- Docs: known limitations updated; whether property writes for the switches are applied is unverified.

## 0.1.0

- First release: vacuum (start, pause, resume, stop, return to dock, locate, fan speed), sensors, selects (suction, water level), switches, button, binary sensor, config flow with reauth, diagnostics, English and Dutch translations.
