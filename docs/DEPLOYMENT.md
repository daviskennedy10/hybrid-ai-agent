# Deployment architecture (templates, not a deployed public demo)

The original system ran in a Google Cloud Run **Instance**, with local Ollama on
a Mac mini reached privately through Tailscale. This repository does not contain
its project ID, URL, principal identities, tailnet name, keys or runtime state.
No live cloud check or deployment was performed to prepare this public copy.

`deploy/cloud/Dockerfile` retains the successful frozen Hermes dependency-install
pattern and non-root UID 10001. It needs a separately checked-out pinned Hermes
source and approved immutable Python/Tailscale image build arguments. No image
was built from the sanitized copy. `deploy/reference/` holds preparation-only
examples and a print-only Instances command renderer reviewed by fixture tests;
it is not an executable production provisioning plan. The reference Dockerfile
uses the same frozen installation pattern but deliberately runs a fail-closed
preparation check rather than starting the cloud supervisor. Neither is a one-command public deployment product.

For an independent deployment, provision your own workload identity, private GCS
namespace and secret mounts, initialize state, configure an allowlisted Telegram
recipient and gateway principal, and use the explicit Cloud Run Instances API.
The cloud supervisor requires non-root execution, no effective capabilities,
valid durable state, private Tailscale authentication and a dedicated OAuth profile.
Its inherited long-run credential-margin check remains intentionally conservative.

The private bridge accepts only an explicitly configured MagicDNS host via
`HYBRID_INFERENCE_HOST`. It validates TLS and allows a fixed subset of inference
paths; it does not accept client-supplied destinations. Restrict a cloud-specific
tailnet tag to the Mac inference proxy port. Keep Ollama bound to loopback. Do not
use Funnel, public Ollama, LAN-wide bindings, host Docker sockets or privileged mode.
The Tailscale pattern is adapted from https://tailscale.com/docs/install/cloud/cloudrun.

Cloud Storage snapshots and leases address durability/single ownership; process
readiness alone cannot prove restoration. Observe post-restart memory and alert
ledger behavior and preserve actual timestamps. No 48-hour uptime claim is made
for this public copy or inferred from the submitted project's elapsed time.

The original deployment also needed a writable startup copy of the read-only
mounted Hermes profile. The private, instance-specific repair command is excluded
here. An independent deployment must provide a writable isolated runtime profile
while keeping mounted source secrets read-only. The sanitized Dockerfile alone
does not establish that integration; this is a remaining deployment-template
validation task, not a demonstrated new deployment.
