# Changelog

## 0.7.0b1 (beta)

This beta is for testing on RV1 models; the current stable release is 0.6.4.

Find rooms on the RV1 models without a schedule. Not verified yet: no user has confirmed this on a real robot.

- New: on a model that has them (RV1 Pro Max, RV1L Pro), the integration also reads the properties `swp_allareaConfig`, `swp_areaConfig`, `swp_allCleanPlan`, `area_clean_plan`, `swp_cleanSequence` and `sweeper_sequence` (room configuration, cleaning plans and cleaning order) and `map_name`. The room ids in them are added to the rooms of the schedules, of `sequence` and the rooms you named. A room that is only found there has no name from the Imou app ("Room -127"). What these properties hold on a real robot is unknown: `area_clean_plan` was empty on both RV1 robots that were checked. If they are empty, you still need a schedule to get your rooms.
- The properties are read in the same call as `sequence`, once an hour, so there is no extra API call. If that combined call fails, only `sequence` is read; after 3 such failures in a row the other properties are not requested again until Home Assistant restarts. A property that is not in the response counts as "read, empty". A failed read is retried after 15 minutes without reading the schedules again.
- Several maps: items with a map id only count for the current map, when that is known. That is the map id of the robot's schedules (-1, "whole house", is ignored), else the one map that `map_name` lists (also when the schedule's map is not among the listed maps). If no item has that map id, or the current map is unknown, and the items carry two or more different map ids, the items with a map id are dropped (room ids repeat per map). Items without a map id are always used. Zeros and values that are not numbers are ignored.
- Diagnostics: the probe also shows the raw values of these properties.
- No change for the RV-L11-A: its model has none of these properties, so it makes no new call and gets no new entity. It still needs a schedule.
- Docs: the README rooms section describes the new sources and that they are not verified yet.

## 0.6.4

- New: reset buttons for the consumables. **Reset side brush**, **Reset main brush**, **Reset filter** and **Reset sensors** set the usage counter of that part back to zero, so the usage sensor starts again at 0 after you replaced or cleaned the part. They are in the configuration category and call the robot's `reset_parts` service. Verified on the RV-L11-A: **Reset sensors** set the robot's sensor counter to 0 and left the other counters alone; the other three buttons send the same command with another part.
- A button is only created when the robot's model has `reset_parts` and lists that part by name. The RV1 models list more parts (mop, dust bag, cleaning fluid, main brush cover, battery); these have no button yet.
- The buttons are enabled by default on the RV-L11-A, and disabled by default on every other model, because nobody has confirmed yet that the reset works there. You can enable them in the entity settings.

## 0.6.3

Feedback on the Imou RV1L Pro (issue #8, version 0.6.2).

- Change: the four dock sensors (Dock connected, Clean water tank, Dirty water tank, Dust bag) are now disabled by default on every model. The RV1L Pro reports the dock status as all zeros, also while it is charging on its dock, and the RV1 Pro Max and the L11-A report nothing, so no model is verified to show real dock status. You can enable them yourself in the entity settings. They are still created after the robot's first online poll, and sensors you already have keep their enabled state.
- Docs: the RV1L Pro is confirmed by the user for return to dock, water volume (medium matches the app) and carpet boost. Dock status is not supported on this model.

## 0.6.2

Changes after the HACS review of the default-store submission.

- Removed: the **Auto-empty** and **Resume after charging** switches. They wrote `auto_dust` and `keep_clean` to the robot, but no model reports these settings (not the RV-L11-A, RV1 Pro Max or RV1L Pro) and the RV1L Pro answers a write with DV1026 (not supported), so nobody could confirm that they do anything. They may come back when a model is verified to accept and report them. The integration also no longer polls these two properties.
- If you installed an earlier version, the two removed switches stay in your entity registry. Go to Settings, Devices & services, Entities, select the two old switches and delete them.
- Kept: the **Carpet boost** switch (every model reports it) and the message "This robot does not support that setting" for a robot that answers DV1026.
- Docs: the empty-bin button is now in the list of verified behaviour of the RV-L11-A. The owner confirmed that the dock empties the robot's bin when you press it.

## 0.6.1

Fixes from the feedback on the RV1 Pro Max (issue #1) and the first report of the Imou RV1L Pro (issue #8).

- Fix: return to dock on the RV1L Pro. This model has no `back_charging` or `back_station`, only the property `swp_backStation`; the integration now writes "start return" (1) to it. The model support report and the vacuum's return-to-dock feature count it, so the "return to dock missing" repair notice no longer appears for this model. The order stays `back_charging`, `back_station`, `swp_backStation`.
- Fix: settings and dock sensors that a robot does not support. The switches (auto-empty, carpet boost, resume after charging) and the dock sensors (Dock connected, Clean water tank, Dirty water tank, Dust bag) are now created after the robot's first online poll, so "disabled by default" is decided on what the robot really reports. A robot that is offline when Home Assistant starts gets them as soon as it is online. The dock sensors are disabled by default when the robot does not report `station_status` (RV1 Pro Max). A setting or sensor the robot never reported stays available and shows unknown (a robot may accept a write without ever reporting it, or report a property only in some states). When a robot answers a switch write with the Imou error DV1026 ("device does not support this function"), you get the error "This robot does not support that setting" and the switch is unavailable until the integration reloads. Entities you already have keep their enabled state; disable or delete the ones you do not need.
- New: rooms from the cleaning order. A model with the property `sequence` (RV1 Pro Max) reports the ids of its rooms there. They are read together with the schedules, once an hour, and added to the rooms of the schedules and the rooms you named. A room that is only in the cleaning order has no name from the Imou app ("Room -127"). Room ids can be negative; this works for the names, the Clean buttons and the segments. That `sequence` lists all rooms was seen in the diagnostics of one robot and is not verified for others; room cleaning is confirmed only for the two rooms of the user's schedule. Zeros in the array are ignored. A response without `sequence` counts as "no rooms", and when only that read fails, only it is retried (after 15 minutes), not the schedules; the same the other way round.
- Docs: consumables on the RV1 Pro Max differ per robot: one robot reports all counters as 0, another reports them (for example 180 h) except the sensors counter. This corrects "Known: the RV1 Pro Max reports its consumable counters as 0" of 0.5.0.
- Change: the suction level "off" is no longer offered as a fan speed or in the Suction select on models that list it (RV1 Pro Max, RV1L Pro): the Imou app does not offer it, and setting it from Home Assistant deselects every level in the app. A robot that reports it still shows "off".
- Change: the RV1 `mop_speed` select is now called **Water volume** with the options Off, Low and Medium (before: Mop intensity with Gentle, Standard, Strong). These are the app's names for the same three values. The unique id is unchanged, so automations that use the entity keep working, but the option names changed. The app also has High; the things model has no value for it, so it is not offered.
- Fix: RV1 schedules. The Schedules sensor now shows `time` (for example `23:00`) and `days` (for example `["mon", "tue"]`) in the same format as the L11-A. The start time is the local time of day and the days count 1 as Monday to 7 as Sunday. `start_raw` and `once` are still shown.
- Change: the Mop fitted binary sensor shows "Fitted" and "Not fitted" instead of On and Off.
- Fix: the Charging binary sensor falls back to the sign of the battery value (negative means charging on RV1 models) when the robot reports no `power_status`: on for a negative battery value, otherwise unknown (not off).

## 0.6.0

- New: the Schedules sensor shows the local time of day (`time`, for example `14:00`) and readable days (`days`, for example `["sun"]`) of repeating L11-A schedules; before, a repeating schedule had no time. Period 0 is Sunday (verified); 1 to 6 as Monday to Saturday is assumed. A one-off schedule is unchanged.
- New: a **Clean <room>** button for every room that is known (rooms of the schedules and rooms you named), for robots whose model supports room cleaning, also on Home Assistant versions before 2026.3. A room without a name reads "Clean room 3". New rooms get a button when they show up; a button whose room is no longer known becomes unavailable.
- New: the options now start with a menu. **Link rooms to areas** shows one area picker per room: the room takes the area's name and, on Home Assistant 2026.3 or newer, the link is written to the vacuum's area mapping (and its last seen segments) for `vacuum.clean_area`. The Imou app does not show room numbers, so the Clean buttons help to find out which number is which room. **Rename rooms (advanced)** is the existing text list.
- Rooms that you named (or linked to an area) stay known when no schedule lists them any more, so their buttons and the options keep working after you edit a schedule in the app. After the update this can bring back a room you named earlier: it gets a **Clean <room>** button and, on Home Assistant 2026.3 or newer, possibly a one-time "rooms changed" repair. To forget a room, remove its line under Configure → **Rename rooms (advanced)**.
- Schedules stay read-only: writing them back through the cloud API loses the room/whole-house setting, so there are no schedule switches.
- The Imou error code DV1062 (device has no heartbeat) is treated as "device offline", like DV1007.
- The Imou error code DV1030 (device asleep) while polling is treated like the status "sleep": the last data stays and it is not counted as a failed poll.

## 0.5.0

- Imou RV1 Pro Max schedules (issue #1): the `clean_list` service of this model returns an array of structs. The integration reads it through the members of the things model: enabled (state), whole house or rooms, once or repeating, the days (`select_day`) and the raw start time. The Schedules sensor shows `start_raw` and `once` for these schedules; `start` stays `null` because the meaning of `start_time` is unknown (the Imou app shows 02:00 for 82800). Rooms of a room schedule (`area_array`, several formats are accepted) feed the room cleaning and the options like those of the L11-A. The L11-A format is unchanged.
- New: Mop intensity select (gentle / standard / strong) for a robot whose model has `mop_speed` (RV1 Pro Max). It is set with a plain property write and polled with the other values.
- New: dock status for a robot whose model has `station_status` (RV1 Pro Max): Dock connected binary sensor and Clean water tank, Dirty water tank and Dust bag sensors. Only the members the model has are created; unknown labels show as `unknown_<n>`.
- Known: the RV1 Pro Max reports its consumable counters as 0.

## 0.4.0

- Other Imou robot models are mapped from their own things model. The statuses, the error codes and the Wi-Fi levels are matched by the labels in the model (Chinese labels such as 待机中 or 充电中) instead of by number, so a robot that numbers its values differently still shows the right status and activity. A label that is not known shows as `unknown_<n>`; labels in a language the integration does not know fall back to the number tables, and values a model does not declare are only filled from the number table when the declared labels agree with it. Unknown values show with the raw value and the model label as attributes. A model without labels keeps using the number tables. The L11-A and the RV1 Pro Max show exactly what they showed before.
- Which statuses count as active (fast polling) or paused (start continues instead of starting) is defined per status, not per number.
- New: a Charging binary sensor for a robot whose model has `power_status` (RV1 Pro Max). It is on while the robot reports "charging". A robot without `power_status` gets no such entity.
- New: a clear notice when a model is not fully supported. When a core function is missing (start and stop, return to dock, battery or status) or a value is not recognised, Home Assistant shows a repair issue that names what is missing or unknown and links to a prefilled GitHub issue form. The issue disappears when a later setup finds nothing missing, when the robot is no longer found, or when the integration is removed. The issue id does not contain the robot's serial. Nothing changes for the L11-A and the RV1 Pro Max.
- A model that is not verified yet gets one line in the log at setup, asking to share the diagnostics.
- Diagnostics: a `support_report` per robot with the functions that were found, the missing core functions and the unknown labels.

## 0.3.1

- Fix: the battery showed 0 % on an Imou RV1 Pro Max (issue #1). While charging it reports a negative value (-38 for 38 %); the integration now shows the absolute value.
- Return to dock also works through the `back_station` property (RV1 Pro Max), next to `back_charging`.
- Suction levels come from the model's enum when `WindPower` is one: off, quiet, standard, strong and max on the RV1 Pro Max. The L11-A keeps quiet, standard and max.
- New work statuses 14 to 19: returning to wash the mop, washing the mop, drying, mapping, mapping paused and return to wash paused.
- Room cleaning sends `partition_area` as an array of room ids where the model asks for an array (RV1 Pro Max); the L11-A still gets JSON text.
- The diagnostics probe also reads the RV1 Pro Max room and map properties (`partition_area`, `map_name`, `sequence`, `area_clean_plan`), in the same single property call.
- English only: the Dutch translation is removed. Home Assistant falls back to English.

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
