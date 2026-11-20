# Changelog

## 0.7.1b5 (beta)

This beta cuts the Imou API calls per robot. The stable release is 0.7.0. Checked on an RV-L11-A in Home Assistant 2026.11.2: a docked robot is polled every 15 minutes, and after a command every 30 seconds for 2 minutes. Not verified yet: the number of calls over a full day, the schedule-start poll and the read after a clean on a real robot.

- Changed: fewer API calls. A robot that rests in its dock (charging, standby or drying its mop) is polled every 15 minutes (was 5), and an active robot every 60 seconds (was 30). A paused, error, mop-washing or unknown status stays at every 5 minutes; an asleep or updating robot is polled like a resting one (it used to follow its last status, which could be 30 seconds). A robot that rests all day uses about 100 calls a day, and every hour of cleaning adds about 60 more, so roughly 100 to 250 calls per day per robot is typical (about 3,000 to 7,500 per month; it was 300 to 600 a day, about 9,000 to 18,000 per month). The schedule and room reads cost about 150 to 350 calls per month, not about 720.
- New: a resting robot is polled about 1 minute after the start time of each enabled schedule (local time of Home Assistant), and every minute up to 3 minutes after the start while it still rests. A scheduled clean shows in Home Assistant within about a minute, once the integration has read the schedule (within 6 hours, or right after the next clean). An RV1 schedule that runs only once has no date and gets no early poll.
- Changed: the schedules and the room properties are read every 6 hours (was once an hour), right after each clean ends (also after a mop wash or a pause in between; emptying the bin in the dock does not count) and on demand (when you open the room mapping, and before a room clean on a robot with several maps). A map switch in the Imou app is noticed at the next of these reads, before a room clean, or when you open **Link rooms to areas**.
- Changed: after a failed poll the next poll comes after 60 seconds, as before, but entities become unavailable after 3 failed polls in a row or when the last good poll is older than 30 minutes (was 15). During a longer outage the interval grows to 120, 240, 480 and 900 seconds (was up to 300).
- Trade-off: a clean that you start from the Imou app or with the robot's button, while the robot rests, shows in Home Assistant within 15 minutes (was 5 minutes). Starts from Home Assistant and scheduled cleans still show within about a minute.
- Docs: the README polling table, the quota section and the FAQ, and the call estimate in the docs pages, are updated.
- Docs: confirmed by users after 0.7.1b4: the learned rooms on the RV1L Pro and the RV1 Pro Max (on the RV1 Pro Max also on a map without a cleaning order), and on the RV1 Pro Max the **remaining** sensors (they match the app) and the API call counter (it matches the Imou console). The README rooms section and the RV1 Pro Max row no longer call these unverified.

## 0.7.1b4 (beta)

This beta is for testing on the RV1L Pro and the RV1 Pro Max; the stable release is 0.7.0. Not verified yet: the repair notice for a used-up quota in a real Home Assistant, and the active map of a robot with an empty cleaning order. (The active map with an empty cleaning order was confirmed after this beta, on an RV1 Pro Max with two maps.)

After feedback from testers: one Imou developer console showed about 4,000 API calls a day for a single robot, where the integration should make roughly 300 to 600; a second robot with two maps never learned rooms; and the RV1 Pro Max app showed the lifetimes of its consumables.

- New: an API call counter. Every request to the Imou API is counted (including the access token): in total, per API method, per purpose and per local day (the last 8 days, in memory only; it starts again when the integration restarts). The diagnostics download has it at the top level as `api_calls`: `since`, `total`, `per_method`, `per_purpose`, `per_day` and `per_day_per_purpose`. The purposes are `setup`, `poll`, `poll_without_partition_area`, `schedules`, `room_properties`, `on_demand`, `learn`, `command`, `token` and `other`. Please attach it when your console shows more calls than expected.
- New: a used-up API quota is recognised: the error code `OP1013`, or a message that has "exceed", "limit" and "times" and neither "frequen" nor "second" (so a per-second rate limit is not taken for a used-up quota). The integration raises one repair notice per config entry ("Imou API quota used up": the free quota is 30,000 calls per month per App ID and renews on the 1st) and logs one warning with the raw code and message. It polls only every 30 minutes (`QUOTA_RETRY_INTERVAL`, from the first hit on), skips the schedule, room and learning reads, and refuses commands with a translated error (no call, no follow-up refresh) while the quota is used up. Any answer that is no quota error (also "robot offline" or asleep) ends that state, deletes the notice and returns polling to normal; with two robots the notice can come back once while the other robot is still blocked. At startup a used-up quota is no longer a plain "could not connect": the repair notice appears, the setup waits 30 minutes (`QUOTA_RETRY_INTERVAL`) and makes no API call on Home Assistant's earlier retries; a successful setup clears the wait and the notice. Unloading or removing the entry deletes the notice (a later setup creates it again if the quota is still used up).
- Fix: a room-properties read that was skipped or failed (for example by the quota, while the poll itself worked) can no longer confirm the map for learning: the old map was taken for the active one.
- Fix: an active map found only by the empty-order rule is used for learning only after two reads of the room properties gave the same map (the trace says `active_map_unconfirmed` until then). Display and cleaning still use the rule at once.
- Fixed: a robot with several maps and an empty top-level `sequence` never got an active map, so no room was learned. When the robot reported an empty `sequence`, at least two maps are listed, exactly one of them has an empty or no cleaning order and every other one has an order, that map is now the active map. A `sequence` that was not reported does not count as empty. If both maps have an empty order the active map stays unknown (set a cleaning order on every map in the Imou app).
- New: a room-learning trace in the diagnostics: per robot `room_learning`, the last 10 observations (time in UTC, `work_status` number and key, the `partition_area` room ids, the active map id, whether the robot has several maps, and the result `not_room_clean`, `empty`, `active_map_unknown`, `map_read_failed`, `learned` or `nothing_new`). An observation is added when the status, `partition_area` or the outcome changes, so repeated polls of the same clean do not push the useful ones out. No names.
- Changed: RV1 Pro Max consumables. The app shows the same lifetimes as the RV1L Pro (side brush 150 h, main brush 240 h, filter 150 h, mop cloth 180 h, main brush cover 200 h, battery 800 h), so the six **remaining** sensors now exist on this model too. The used hours plus the remaining hours of the app give these totals, which confirms that the `life` counters are minutes. **Reset mop** is confirmed on the RV1 Pro Max (the app went back to 100 %) and is enabled by default there; its other resets stay disabled (`CONFIRMED_RESETS` and `PART_LIFETIME_HOURS` in `support.py`).
- Changed: **Sensors usage** (the `sensor_time` counter) is created enabled by default only on models whose sensors reset is confirmed (the RV-L11-A, where the counter is real). The RV1 apps show no counter for the sensors and the cloud keeps it at 0, so on every other model it is now created disabled. Existing entities in the entity registry keep their state: an RV1 robot that already has an enabled Sensors usage keeps it until you disable it. Nothing changes for the RV-L11-A.
- Docs: a README section "API calls and the Imou quota" (the free quota, what the integration uses per robot as an estimate, not sharing the App ID, what happens when the quota is used up, where the counters are), and the rooms, sensors and reset sections are updated. The comment about the monthly use in `const.py` said about 12k a month; that was wrong.

## 0.7.1b3 (beta)

This beta is for testing on the RV1L Pro with several maps; the stable release is 0.7.0. Not verified yet: the new segment list and the area names in Home Assistant itself.

After feedback from an RV1L Pro user with three maps: every map switch in the Imou app gave the repair notice "vacuum segments changed" and the new map was only used after opening Home Assistant's area mapping again, and rooms linked to areas there still had their old button names.

- Changed: on a robot with several maps, the segments of `vacuum.clean_area` (Home Assistant's room mapping) are now the rooms of **all** maps, not only the active one. The ids stay `<map id>:<room id>` and every room keeps the names of its own map. Where the Home Assistant version has segments that support groups (the map grouping needs such a version; older versions show the segments ungrouped), the group is the map's name, or "Map <id>" when it has none. The rooms of a map are read from its schedules, its room properties, the rooms learned on it and the rooms you named on it; the cleaning order only belongs to the active map.
- Fixed: a map switch no longer raises the "segments changed" repair notice for rooms that were already shown: the list of segments does not change any more. The comparison with the mapped rooms no longer filters on the active map, so the notice still appears for a room that is really new, on any map. A room that only appears in a map's cleaning order once that map is active can still raise it once. The first update from 0.7.0 on a robot with several maps raises it once, because the rooms of the other maps are new to Home Assistant. Rooms that were mapped before stay listed on every map. A default name does not replace a name seen before, unless that name came from an area: when the link or the area is gone, the segment and the Clean button both fall back to the default name.
- Changed: an area that is linked to rooms on several maps cleans only the rooms of the active map. The rooms of other maps are skipped with one line in the log; the request is only refused (as below) when none of its rooms is on the active map.
- Changed: a room of a map that is not active is still refused, now with a message that names the map: "This room is on the map "<map>"; switch to that map in the Imou app first." The Clean buttons are still created for the active map only.
- New: a room takes the name of the Home Assistant area its segment is linked to (the vacuum's `area_mapping` in the entity registry options, set in Home Assistant's mapping dialog or by **Link rooms to areas**). The name of a room is, in this order: the name you gave it under **Configure**, the area (the first area by id when several link it; an area that no longer exists is skipped), the room type from the Imou app, "Room <id>". This applies to the Clean buttons (the map name stays as a suffix, for example "Clean Kitchen (Ground floor)") and to the segment names. Names update without a restart when the mapping or an area name changes. Entity ids and unique ids do not change.
- Linked area names also apply on robots with one map, including the RV-L11-A (plain ids, no groups), but only to rooms without a name of your own: the names you gave the rooms keep priority. **Link rooms to areas** still stores the area's name as your own name and writes the same `area_mapping` as Home Assistant's dialog, so a later rename of the area does not rename those rooms.
- Changed: Reset mop, Reset main brush cover and Reset battery are confirmed on the RV1L Pro (the counters in the Imou app went back to 100 %), so with side brush, main brush and filter all six reset buttons of that model are enabled by default (`CONFIRMED_RESETS` in `support.py`). The sensors reset stays unconfirmed and disabled there, and nothing changes for other models.
- Docs: the README sections on rooms, segments, map switching, area names and the reset defaults are updated.

## 0.7.1b2 (beta)

This beta is for testing on the RV1L Pro; the stable release is 0.7.0. Not verified yet: the unit of the usage counters on an RV1 robot.

The Imou app does not show the used time of a consumable, it shows the remaining percentage of a fixed lifetime. This beta adds that percentage.

- New: sensors **Side brush remaining**, **Main brush remaining**, **Filter remaining**, **Mop cloth remaining**, **Main brush cover remaining** and **Battery remaining** (%), only for a robot whose model has known lifetimes and whose `life` struct has the counter. The value is 100 minus the used time as a percentage of the lifetime, rounded and never below 0. Diagnostic category, enabled by default, read-only. No remaining sensor for the sensors: the app has no counter for it.
- The lifetimes come from the Imou app of an RV1L Pro (all parts showed 100 % while every counter was 0): side brush 150 h, main brush 240 h, filter 150 h, mop cloth 180 h, main brush cover 200 h, battery 800 h. They are the table `PART_LIFETIME_HOURS` in `support.py`.
- Still assumed: the counters are in minutes, like on the RV-L11-A. That is not verified on an RV1 robot, so compare the percentage with the app. (Confirmed in 0.7.1b4 on the RV1 Pro Max.)
- Other models (RV-L11-A, RV1 Pro Max) get no new sensor; they get it once their lifetimes are known.
- Docs: the README sensor list is updated.

## 0.7.1b1 (beta)

This beta is for testing on RV1 models; the stable release is 0.7.0. Not verified yet: no user has confirmed the learned rooms on a real robot. (Confirmed later by users on an RV1L Pro and an RV1 Pro Max.)

Learn rooms from the room cleans you start in the Imou app, for RV1 robots that report no room list at all (empty `sequence`, no room properties, a whole-house schedule).

- New: on a model that has the property `partition_area` (the RV1 models), it is added to the regular property poll, in the same `getIotDeviceProperties` call, so there is no extra call. Zeros and values that are not integers are ignored. A real RV1 Pro Max showed `[-128]` in it after a room clean started in the Imou app. If the cloud rejects the poll because of this ref (the poll fails, but works without it), the ref is left out for the rest of the run, with one info line; a restart tries again.
- New: the room ids it holds are learned only while the robot does a room clean (`work_status` is the room-clean status). The property keeps the rooms of the last clean, which may have been on another map, so an idle or docked robot learns nothing. With several maps the room properties are read fresh (one extra call, the cache is bypassed) on the first poll of a room clean, and the rooms are filed under the active map; while the active map is unknown (or that read fails) nothing is learned. Whether a clean started by a schedule or by the cleaning order fills `partition_area` is not verified.
- New: learned rooms are stored (Home Assistant storage, per config entry and robot). The store is written after a delay when a new room is learned, and at once when you forget the rooms, so they survive a restart. They join the known rooms with the same rules as the other sources: default name "Room <id>", Clean buttons, segments and entries in the options flow, and with several maps only the active map's rooms.
- New: **Configure** has **Forget learned rooms** (robots with `partition_area` only), with a confirmation. It clears the learned rooms of that robot; a room is learned again the next time it is cleaned.
- Diagnostics show `learned_rooms` and the raw `partition_area`. The learned rooms of a removed config entry are deleted with it.
- No change for the RV-L11-A: its model has no `partition_area` property, so no extra poll ref, no learning, no store writes (it only reads an empty store at startup) and no new menu item.
- Docs: the README rooms section explains the learning and that it is not verified yet.

Consumables for the RV1 models, after feedback from an RV1L Pro user: Reset filter, Reset main brush and Reset side brush work there, the Imou app has no sensor counter (the integration always shows 0 for it), and the app also shows the mop cloth, the main brush cover and the battery.

- New: usage sensors **Mop cloth usage**, **Main brush cover usage** and **Battery usage**, only on a model whose `life` struct has `mop_time`, `cover_time` and `battery_time` (the RV1 models). Same unit (hours) and diagnostic category as the other usage sensors, enabled by default, read-only. The unit of these three counters is assumed to be minutes, like the others (confirmed in 0.7.1b4 on the RV1 Pro Max). The RV-L11-A gets nothing new.
- New: buttons **Reset mop**, **Reset main brush cover** and **Reset battery**, only when the `reset_parts` list of the model has the parts mop, main brush cover and battery. They are disabled by default on every model, because none of them is confirmed.
- Changed: whether a reset button is enabled by default now depends on the robot model and the part. Enabled: side brush, main brush, filter and sensors on the RV-L11-A (tested by the owner); side brush, main brush and filter on the RV1L Pro (confirmed by a user). Every other combination stays disabled, as before (this changes nothing for the RV-L11-A; the RV1L Pro's three confirmed buttons are now enabled for new entities). Entities that already exist keep their enabled state.
- Docs: the README entity lists are updated.

## 0.7.0

Rooms for the RV1 models, from the betas 0.7.0b1 and 0.7.0b2. Confirmed by two users: an RV1L Pro and two RV1 Pro Max robots, one of them with two maps.

- New: on a model that has them (RV1 Pro Max, RV1L Pro), the integration also reads the room configuration, cleaning plans and cleaning order (`swp_allareaConfig`, `swp_areaConfig`, `swp_allCleanPlan`, `area_clean_plan`, `swp_cleanSequence`, `sweeper_sequence`) and `map_name`, in the same hourly call as `sequence`. Their room ids are added to the rooms, so a robot that reports them needs no schedule. A user confirmed that setting a cleaning order in the Imou app makes all rooms of a map appear. A robot that reports no rooms at all still needs a schedule with the rooms (it may stay switched off).
- New, robots with several maps: the integration works out the active map and only shows its rooms. Names, area links, segment ids (`<map id>:<room id>`) and Clean buttons are kept per map, so a name no longer shows up on the other map; the buttons of the other map are unavailable. Confirmed by a user with two maps.
- New, robots with several maps: before a room is cleaned, the active map is read again (one extra call) and a room of another map, an old plain room id or an unconfirmed map is refused with a clear message.
- If the combined room read fails, only `sequence` is read; after 3 such failures the other properties are skipped until a restart. Diagnostics show the raw values, `active_map` and `multi_map`.
- Room names saved before on a robot with several maps are plain (without a map): name and link those rooms again under **Configure**.
- No change for robots with one map and the RV-L11-A: plain room ids, names and buttons, and no extra calls.

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
