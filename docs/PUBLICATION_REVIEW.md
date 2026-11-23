# Public release review

This is a separate portfolio candidate, not the submitted/deployed project.
No repository has been created on GitHub and nothing has been pushed. Any local
Git index belongs only to this candidate; original assignment history is excluded.
The proposed GitHub name is **hybrid-ai-agent**.

## Included

Application-owned routing, isolated Hermes adapter, Telegram ingress, four real
skill adapters, scoped file memory, proactive SQLite scheduler, GCS persistence,
gateway/health/security code, synthetic regression fixtures, reviewed deployment
examples, and six original public-synthetic benchmark outcomes. Original names and
coursework safety gates are retained where changing them would weaken boundaries.
The fresh-clone test and scoped security/dependency results are recorded in
[reports/verification.json](../reports/verification.json).

## Excluded from the public Git index

- Original `.git` history, remotes, provisioning/build/migration journals and SDK logs.
- All credentials, account IDs, user/chat IDs, passwords, OAuth profiles/tokens,
  private calendar feeds, inbox content, secret versions and real configuration.
- Cloud project/bucket/service-account identifiers, deployed/grader URLs, private
  tailnet names, host-specific Caddy/LaunchAgent files and operational markers.
- Operational evidence exports, private logs, memory/scheduler/activity databases,
  snapshots, account/persona runtime state and synthetic account passwords.
- Submission ZIPs, packaging gates, grader client/credentials, course PDFs, course
  process-log history, assignment checklists, recorded video and large media.
- Original Hermes checkout/history, virtualenvs, bytecode, caches, generated builds,
  downloaded wheels/model weights and optional dependency installations.
- Standalone weather-tap publication history/transcripts. The existing separate
  weather release is not republished by this preparation.

Ignored local environments may exist for testing; they are not public files.
Example principal `101`, fictional preference labels, localhost URLs and synthetic
fixture email strings are demonstration values, not account access. The one
Telegram-token-shaped fixture is explicitly fake, mocked and hash-reviewed.

## Publication authorization and remaining checks

The owner reports explicit professor permission to publish the full implementation;
the earlier course-policy hold is resolved. The local initial commit is authorized.
Remote repository creation and pushing are **not** authorized in this checkpoint.
The root MIT license is scoped to author-owned application code; third-party notices
are retained and dependencies/model weights are not re-licensed.

Before pushing:

1. Run `python scripts/release_checks.py` from normal Terminal with network access.
   This batches Yelp detect-secrets over an exact staged snapshot and pip-audit over
   the core lock, enumerated optional Hermes dependency inventory and optional
   GCS SDK requirements (including resolver-discovered transitive dependencies). Secret
   verification via provider network calls is disabled. No possible credential is
   sent for validation; advisory queries use public package names/versions only.
2. Review every independent scanner finding. Existing synthetic fixtures and hashes
   can cause false positives, but the independent results are never automatically
   waived. A successful tool exit alone is not considered a clean secret scan.
3. Repair or validate GitHub CLI authentication externally. The installed CLI's
   sandbox check reported an invalid token; no credential was printed or changed.
4. Personally review public source, examples, benchmark outputs and attribution.
   No automated scan certifies all semantic privacy or copyright ownership.
5. Treat independent live deployment as a separate task: no container build or cloud,
   GCS, model or bot operation was executed for the portfolio release.

Registry DNS failed inside the sandbox, preventing detect-secrets and pip-audit
installation. The existing stdlib/path/known-value checks passed; independent
secret/advisory scans are **unavailable, not passed**, and no zero-vulnerability
claim is made. Raw external tool output is saved only under ignored, owner-only
`reports/local/`. The initial commit can be reviewed locally while these remain
explicit release blockers.

## Commands for independent review

```sh
uv sync --frozen --python 3.14
uv run --frozen python scripts/test_offline.py
uv run --frozen python -m benchmarks.verify
uv run --frozen python scripts/scan_public.py
uv pip check --python .venv/bin/python
```

Independent tool documentation: https://github.com/Yelp/detect-secrets and
https://github.com/pypa/pip-audit. The initial release uses these tools only as
external checks, not vendored source or runtime dependencies.

The requested local commit message is `Initial release: hybrid AI agent`.
No command in the release checker creates a repository, commits or pushes.

## Completed checks and remaining limits

- Fresh local Git clone: frozen cache-backed dependency install, 243 passed /
  17 optional skipped, policy/memory demo, benchmark replay, scanner and core
  dependency compatibility all passed. The temporary local seed had one sanitized
  test-only commit; neither this candidate nor the submitted project was committed.
- Separate pinned Hermes: 254 passed / six live model tests skipped. Real framework
  request construction used mocked provider transports; no model inference ran.
- Raw optional-framework compatibility check reports one intentional upstream
  Pillow override. No framework patch or dependency downgrade was made. Review
  this warning for the optional install; the core two-package environment is clean.
- Original-history inspection covered 171 reachable/reflog blobs. Private-value
  matches were zero; one pattern match was the explicitly synthetic token fixture.
  No original Git history or private matching snippets are copied.
- The local-only preparation scan also checked copied code against known private
  values and encoded forms: zero matches. The reusable scanner checks patterns,
  paths, staged blobs and reachable history. This is not semantic DLP or a current
  advisory scan. The MIT grant is limited to author-owned application code.
- All 93 source files read for copying retain their original hashes in the submitted
  project. Live runtime files were not read or modified for tests.

## Initial-release checkpoint

The latest public suite ran **266 tests: 260 passed and six live-model tests skipped**.
The local staged/path scanner, benchmark replay, lock check and core compatibility
check passed. Yelp detect-secrets and all pip-audit scopes were attempted but could
not install because public-registry DNS failed. No independent clean scan or zero
vulnerabilities is claimed. `gh` is installed, but its sandbox authentication check
reports an invalid token; no token was printed, reset or changed.

The owner-authorized local commit uses `Initial release: hybrid AI agent` and a
repo-local GitHub noreply author address. No remote creation or push is part of
this checkpoint. The earlier preparation snapshot in `reports/verification.json`
is historical; current results are in `reports/initial-release-review.json`.

Run the batched checks externally before publication:

```sh
cd ~/hybrid-ai-agent-public
uv run --frozen python scripts/release_checks.py
gh auth status --hostname github.com
```

Review the ignored private reports if any tool finds possible secrets/advisories.
Do not auto-waive findings or use an empty output file as proof of success. If the
GitHub authentication check still fails in Terminal, use `gh auth login --hostname
github.com --web`; this preparation does not run that login or modify credentials.

Only after reviewing results and authorizing publication, the eventual commands
are `gh repo create hybrid-ai-agent --public --source=. --remote=origin`, followed
by `git push -u origin main`. They have not been executed.
