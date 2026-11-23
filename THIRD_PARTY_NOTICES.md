# Third-party notices and attribution

| Component | Relationship | License verified from local source/metadata |
|---|---|---|
| Nous Research Hermes | Separately installed framework at `b200f5bc60e31588ba2cb60e288029c4a54ba5fa`; reported v0.22.7 | MIT; copyright (c) 2025 Nous Research; full pinned-source notice in `third_party/Hermes-LICENSE` |
| python-dateutil 2.9.0.post0 | Core dependency, not vendored | Dual Apache-2.0 / BSD terms; complete distributed text in `third_party/python-dateutil-LICENSE` |
| six 1.17.0 | Core dependency, not vendored | MIT; complete notice in `third_party/six-LICENSE` |
| google-cloud-storage 3.17.1 | Optional GCS client dependency; no SDK source bundled | Intended upstream Apache-2.0 package; not installed in the core verification environment; review its installed license/transitives before a cloud release |
| OpenAI SDK 2.54.0 | Optional dependency supplied by the pinned Hermes environment | Apache-2.0 in installed metadata; not bundled or re-licensed by this project |
| httpx 0.28.1 | Optional Hermes dependency | BSD-3-Clause in installed metadata; not bundled |
| requests 2.34.2 | Optional Hermes dependency | Apache-2.0 in installed metadata, includes LICENSE/NOTICE; not bundled |

The application MIT notice does not replace these licenses. Do not attribute
Hermes source to this project's author. No framework source/history, third-party
wheels, model weights, Docker binaries, course PDFs or media are distributed.
Library installation must retain its own wheel/distribution license notices.
This is a scoped review, not a complete transitive license audit of all optional
Hermes extras or the eventual container base images.

The Tailscale userspace-container pattern is adapted from
https://tailscale.com/docs/install/cloud/cloudrun. Original comments/reference
attribution are retained; the course reference PDF itself is not redistributed.
GitHub Actions uses a pinned actions/checkout reference; its implementation is not
vendored here. CI is prepared, not yet observed running on GitHub.

Ollama, Gemma model weights, cloud model access, Google services, Telegram, search
articles and weather data have independent terms. No model-weight or external
article/data redistribution license is granted by this repository. Benchmark
prompts and retained responses are public synthetic tasks, not account content.

**Scope of the application MIT license:** author-owned application code and new
portfolio scaffolding. The upstream notice files retain their own licenses and
copyrights. No vendor code, model weights, Docker binaries or dependency sources
are re-licensed as the author's work. Instructor publication permission has been
reported by the owner; it does not alter these third-party terms.

The review found no incompatible third-party source included in the public index.
Optional dependencies and eventual built container images still need their own
complete transitive licensing review. This scoped inspection cannot establish
rights in undisclosed external contributions.
