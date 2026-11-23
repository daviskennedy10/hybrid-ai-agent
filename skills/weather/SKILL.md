---
name: assignment-weather
description: Retrieve current weather and a three-day forecast for a configured fictitious profile, returning source, timezone, units, and structured daily data.
version: 1.0.0
author: CSCI599 Assignment 3
license: MIT
metadata:
  hermes:
    category: productivity
    tags: [weather, structured-data, fictitious-profile, assignment]
---

# Assignment weather

Use `assignment_skills.weather.Weather` through the authorized routed Telegram path. The initial fictitious profile is “Fictional Casey Test”, Los Angeles, America/Los_Angeles.

## Procedure

1. Authorize the principal and use only the configured fictitious coordinates/timezone. No address lookup or personal profile discovery is performed.
2. Fetch Open-Meteo current temperature/humidity/wind/weather code and three daily forecasts. On a transport timeout/unavailability only, fetch and validate real wttr.in JSON instead; accurately label the provider. Neither requires an API key or paid subscription.
3. Validate the response shape, finite numeric values, units, timezone, and aligned daily arrays. On failure, return an explicit tool error, not estimated weather.
4. Return structured `current`, `daily`, `units`, `timezone`, and source/provider/fallback data that a later briefing could consume. Never invent WMO codes for fallback data. Do not implement that briefing or scheduling here.
5. Display source-attributed data in Telegram. Send no chat history to the weather service and keep weather payloads out of logs/model context.

## Telegram examples

`/weather`, “What's the current weather?”, or “What's the weather forecast for Los Angeles?” Other locations require explicitly reconfiguring the fictitious test profile; unsupported locations fail rather than silently guessing.

## Validation

`hermes-agent/.venv/bin/python -m assignment_skills --probe weather`

Tests cover structured current/forecast results, malformed arrays, missing units, nonfinite numbers, authorization, and Telegram dispatch. Live execution through Telegram is required before marking this skill demonstrated.
