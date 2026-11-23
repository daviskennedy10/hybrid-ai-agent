---
name: assignment-calendar
description: Read only the dedicated CSCI599 synthetic Google Calendar; query local-day schedules, busy overlaps, and free working-hour slots.
version: 1.0.0
author: CSCI599 Assignment 3
license: MIT
metadata:
  hermes:
    category: productivity
    tags: [calendar, read-only, timezone, assignment]
---

# Assignment calendar

Use for calendar, today's/tomorrow's schedule, upcoming events, free time, and overlap questions. Telegram requests enter `Router.run` and the assignment dispatcher invokes `assignment_skills.calendar.Calendar`; this package is not automatically installed into upstream Hermes.

## Procedure

1. Require the authenticated Telegram principal to be present in the skill allowlist.
2. Require `assignment_only_confirmed` and the private iCal URL configured locally in `secrets/skills.json`.
3. Fetch only the fixed dedicated calendar feed; require its `X-WR-CALNAME` to be `CSCI599 Stage4 Synthetic`.
4. Parse UTC/TZID/all-day events, bounded recurrence and exceptions; use configured IANA local-day boundaries. Return source-backed schedule/free/overlap results.
5. Keep titles/URLs out of logs and raw tool data out of subsequent model history. Do not create/change events.

## Telegram examples

`/calendar today`, `/calendar tomorrow`, `/calendar upcoming`, `/calendar free tomorrow 30 minutes`, `/calendar overlaps tomorrow`, or “What's on my calendar tomorrow?”

## Validation

`hermes-agent/.venv/bin/python -m unittest discover -s tests -p test_skills.py -v`

`hermes-agent/.venv/bin/python -m assignment_skills --probe calendar`

Expected: a real private Google Calendar read, `route=tool`, no claimed LLM model. Unsupported recurrence is an error, not a guessed schedule. Zero-length events do not block free time. Free slots use 09:00–17:00 local and a default 30-minute minimum. See `docs/STAGE4.md` for supported parsing limits, setup, and live demonstration evidence.
