# Origin, ownership and AI assistance

I developed this project as USC CSCI 599 (Agentic AI) Assignment 3, then prepared
this independently organized portfolio copy. The application-owned router,
Telegram admission/delivery, skill adapters, memory, scheduler, persistence and
security controls are the engineering focus. Legacy `assignment_skills` package
names and synthetic-account guards are retained to avoid disguising the origin
or weakening verified boundaries.

Hermes Agent is **Nous Research's framework**, not my implementation. The adapter
uses its supported AIAgent path at commit
`b200f5bc60e31588ba2cb60e288029c4a54ba5fa` (reported v0.22.7). It is installed
separately; no Hermes source, virtual environment or upstream history is vendored.
The upstream MIT notice is preserved in `third_party/Hermes-LICENSE`.

Codex/ChatGPT assisted substantially with initial code drafts, architecture
inspection, debugging, tests and documentation. Substantial AI-assisted modules
include `router/`, `telegram_bot/`, `assignment_skills/`, `proactive/`,
`agent_memory/`, `persistence/`, `operations/`, deployment/test scaffolding and
benchmark tooling. I defined the requirements and safety boundaries, configured
dedicated test accounts, ran external verification and reviewed results. Exact
served development-model versions are not reliably recorded. Inference model
names in the benchmarks identify tested backends, not the development assistant.

The original Google Cloud/Tailscale design was informed by the course deployment
reference and the official Tailscale guide. The code's adaptation comments are
retained. Course handouts, student submission artifacts and private operational
transcripts are not redistributed. The earlier standalone weather skill remains
a separate project; its release and history are not copied into this repository.

## Publication permission and license scope

The owner explicitly reported that the CSCI 599 professor granted permission to
publish the submitted implementation. That resolves the earlier course-policy
hold for this repository. No private instructor correspondence is redistributed.
The local initial commit is authorized; remote creation and pushing remain separate
owner actions and have not been performed.

The root MIT license applies to the author-owned application implementation and
new portfolio scaffolding. It does not replace Nous Research's Hermes license,
the dependency notices, model-weight licenses or external API/data terms. Hermes
source/history and dependency binaries are not part of the published index. The
retained notices preserve their original copyright holders and license language.
No additional contributor ownership is asserted by the author.
