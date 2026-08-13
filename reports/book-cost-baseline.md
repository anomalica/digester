# Per-book allowance measurement - baseline

Book 1: The Fatima Secret, 374KB, sonnet, effort=low, canonical.
Run started 2026-07-30T14:43:10Z (23:43:10 JST).

## BEFORE (stored series, forest, 10-minute samples)
Sample immediately preceding the run - 2026-07-30T14:40:01Z:

    five_hour  utilization 14.0%   resets 2026-07-30T19:00Z
    seven_day  utilization 12.0%   resets 2026-08-05T11:00Z

Ceilings from the scheduler's gate: session 90, weekly 85 (NOT 100).
So "80% of the week's credits" should be read against a ceiling of 85.

## Instrument caveats (from anomalica/scheduler, and they matter)
1. The weekly figure is INTEGER-ROUNDED - 9, 10, 11, 12, whole points only.
   A 374KB book costing under one point is indistinguishable from one costing
   zero. The smallest book may land entirely inside the rounding.
2. The scheduler's variant lane draws on the SAME window concurrently - the
   five-hour column moved 0 -> 14 in forty minutes, and that is mostly theirs.
   Any weekly delta across this run contains their work unless subtracted.

## Therefore: token counts are the primary instrument, percent is corroboration
The digest stamps input and output tokens per run. That is exact, attributable
to this run alone, and immune to both the rounding and the concurrent lane.
Report tokens first; report the percentage as a cross-check, never as the
measurement.

## AFTER
To fill from the first stored sample following completion:

    ssh root@forest "grep -c . /var/lib/forest/bronze/claude-usage/2026-07-30.jsonl"
    (SSH_AUTH_SOCK=/run/user/$(id -u)/keyring/ssh; host is `forest`, NOT forest.local)
