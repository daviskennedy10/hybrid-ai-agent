# Reproduce without production access

The default path uses no credentials, external requests, cloud resources or Ollama.
Python 3.14.4 was used for this candidate; core declares Python 3.11–3.14, with
other versions not independently tested here. Install Git and uv separately.

```sh
git clone https://github.com/daviskennedy10/hybrid-ai-agent.git hybrid-ai-agent
cd hybrid-ai-agent
uv sync --frozen --python 3.14
uv run --frozen python scripts/test_offline.py
uv run --frozen python scripts/demo.py
uv run --frozen python -m benchmarks.verify
```

The clone URL is the intended publication target, not a repository created by
this preparation. Remote creation and pushing are still pending owner action. The demo exercises policy/code,
not model inference; tests use synthetic transport fixtures. Optional Hermes
integration tests skip when its separate checkout is absent.

## Optional actual Hermes execution

Use a new directory/profile and new credentials that you control. Never copy a
submitted/deployed profile, token, runtime directory or bot here.

```sh
git clone https://github.com/NousResearch/hermes-agent.git hermes-agent
git -C hermes-agent checkout --detach b200f5bc60e31588ba2cb60e288029c4a54ba5fa
uv sync --frozen --no-dev --python 3.14.4 --directory hermes-agent
```

Hermes remains a separately installed MIT-licensed dependency, not this project's
code. Configure its `gemma-local` alias using the structure in
`examples/hermes.config.example.json`, and authenticate a dedicated profile through
the pinned framework's supported interactive login flow. Set `HERMES_HOME` to that
profile when calling the adapter. Obtain entitlement to the recorded models; the
historical names are not a promise of availability to every account. No API-key
replacement for the Codex OAuth path is implemented.

Only after consciously choosing to make a real inference request:

```sh
hermes-agent/.venv/bin/python -m router --prompt 'What is 2 + 2?'
```

That command can fall back to cloud for an eligible request if local inference is
unavailable. `--private` instead enforces local/held behavior. Inspect the successful
attempt's execution metadata; a policy decision or model-list probe is not proof
that a model produced a reply. Logs never need raw prompts or credentials.

## Optional Telegram and integrations

1. Create a **new** Telegram bot and keep its token in ignored `secrets/telegram.env`.
   Set a real allowlisted user ID that you control; example IDs are not permissions.
2. Copy `assignment_skills/config.example.json` to ignored `secrets/skills.json`.
   Keep confirmations false/credentials empty until using dedicated synthetic-only
   accounts. The inherited `csci599...@gmail.com` account-name restriction and exact
   synthetic calendar name are intentional legacy safety gates, not credentials.
3. Scheduler defaults to disabled. Copy `proactive/config.example.json` to
   `secrets/schedule.json`, set the same authorized recipient, and explicitly enable
   only after reviewing its delivery schedule. Do not reuse a live bot token.
4. Local persistence is the default when no GCS configuration exists. GCS mode
   requires your own private bucket, workload identity and validated initial state.
5. Run `hermes-agent/.venv/bin/python -m telegram_bot` only after configuration.
   Never run two pollers with the same token. SIGTERM drains tasks and checkpoints.

Calendar/email have no bundled account data. Email sending remains disabled even
with a purported approval. Search/weather perform real retrieval when explicitly
called; external content is data and cannot change permissions. No public demo
endpoint or production credentials are distributed.

### Optional framework dependency warning

The frozen pin installs successfully from cached artifacts and its offline
integration tests passed. Raw `uv pip check` reports browser-harness 0.1.13's
Pillow 12.3.0 requirement against Hermes's explicit Pillow 13.0.0 override. The
upstream pyproject documents that choice. This project does not exercise browser
tools, does not downgrade Pillow, and does not claim a fully conflict-free optional
framework graph. The core two-dependency environment passes compatibility checks.
