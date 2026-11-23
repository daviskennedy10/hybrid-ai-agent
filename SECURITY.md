# Security boundaries and reporting

Do not open a public issue containing credentials, account identifiers, private
URLs, raw messages, snapshots or OAuth material. A private reporting contact must
be selected before publication; no monitored security inbox is claimed here.

Implemented controls include allowlisted private Telegram chats, scoped memory,
size/time/concurrency limits, password-before-agent execution, constant-time
credential comparison, anonymous-failure isolation, Origin/Unicode validation,
no server-version header, log field allowlists, dedicated synthetic-account
confirmation, disabled email sending, fixed private inference destinations and
checksummed generation-conditional storage updates.

Tests exercise unauthorized requests, malformed payloads, untrusted external text,
retry/dedup behavior and snapshot corruption with synthetic fixtures. Passing
fixture tests is not a penetration test, container escape test, live GCS proof or
claim of complete injection resistance. Regex privacy rules can miss sensitive
meaning. Secret Manager and tailnet ACLs require independent configuration.

Credentials must stay in ignored private files or a secret manager. Use different
bots/profiles/buckets for development and deployment. Never reuse the submitted
agent's identity, pause its services or copy its state to reproduce this project.
Email sending is disabled in code; a model's approval cannot enable it. Scheduled
work does not gain extra permissions.

Internet-facing deployments retain credential-guessing, connection exhaustion,
DoS/cost, token expiry, external-service compromise and dependency risks. Read-only
memory access can still disclose data to an authorized principal. TLS, a strong
password and local rate limits are necessary controls, not a public-hosting SLA.
