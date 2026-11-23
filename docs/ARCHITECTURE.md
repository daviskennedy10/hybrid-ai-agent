# Engineering design

## Execution boundary

`Router.run` is the admission point for Telegram messages, gateway requests and
scheduler tool reads. Rule-based classification runs before `HermesBackend`
constructs an inference subprocess. `router/hermes_worker.py` subclasses the pinned
Hermes AIAgent only to observe its request/response path. It disables native tools,
framework fallbacks and automatic OAuth refresh; assignment-owned adapters execute
supported tools deterministically. The normal Hermes REPL bypasses this adapter,
so it is not a supported ingress for this project.

A successful attempt validates provider, wire model, API mode and endpoint host.
The normalized response model may be synthesized by Hermes and is labelled
accordingly. Health probes report availability, not actual inference.

## Routing and reliability

Privacy rules precede reasoning/length rules. Classification uses both current text
and conversation context; byte budgets are admission heuristics, not tokenizer
measurements. Local Gemma serves simple/private work. Complex reasoning and long
public context use cloud. Bounded local retries and eligible cloud fallback are
explicit. Cloud 429 can fall back locally when policy permits. Private tasks are
held if local inference is unavailable. Regex rules remain imperfect semantic
privacy classifiers; the explicit private flag is the stronger operator control.

## Durable proactive work

`proactive/engine.py` shares the Router's actual tool adapters. SQLite stores
notification keys, status and bounded retry state; `fcntl` locks protect singleton
processes on POSIX. Deduplication survives restarts. Delivery ambiguity is treated
conservatively: no claim of distributed exactly-once Telegram delivery is made.
Timezone-aware schedules and downtime catch-up limits are deterministic. Alerts
use scheduled polling, not IMAP push/event subscriptions.

`agent_memory` stores bounded fictional preferences under opaque user namespaces
using SOUL/context/memory files. `/new` resets conversation history, not durable
memory. Explicit memory commands validate allowed keys and values; external text
cannot silently write preferences. Activity counts represent verified agent work,
not claims that real-world meetings or tasks were completed.

`persistence` makes consistent SQLite backups and a checksummed memory bundle.
GCS generation preconditions implement optimistic concurrency and expiring ownership
leases. Immutable snapshots are uploaded before updating the head pointer. Raw
credentials and email bodies are not part of the snapshot schema. Runtime files
remain local; do not place a live SQLite database on a FUSE object-store mount.

## Deliberate tradeoffs

- Explainable policy and deterministic tools trade semantic flexibility for auditability.
- Disposable workers isolate framework state but add startup latency.
- Small local models preserve local inference choices; the pilot does not show a speed win.
- Read-only OAuth use avoids mutating a shared profile, but expiry is an operator concern.
- One process simplifies polling/scheduling ownership; recovery still depends on durable state.
- Container/HTTP hardening reduces exposure, not all denial-of-service or injection risk.
