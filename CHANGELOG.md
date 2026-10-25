# Changelog

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
