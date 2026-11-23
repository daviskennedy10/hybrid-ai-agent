# Six real paired observations

I ran one synthetic prompt per task category on each backend on November 19, 2026.
These are retained measurements, not fixtures. Replay and independently recompute
the strict answer scores with `uv run --frozen python -m benchmarks.verify`.
The exact original result bytes and SHA-256 remain in `benchmarks/results/`.
The environment file is an explicitly labelled, reviewed derivative.

| Task | Gemma 4 E2B seconds | Strict score | GPT-5.6 Sol seconds | Strict score |
|---|---:|---:|---:|---:|
| Simple facts + arithmetic | 38.115 | 0/4 | 7.708 | 4/4 |
| Dependency planning | 62.128 | 0/4 | 8.562 | 4/4 |
| Synthetic 280-row context extraction | 20.485 | 0/4 | 7.733 | 4/4 |

## Method and honest limits

Identical case prompts/oracles are in `benchmarks/cases.py`: 231, 461 and 30,522
UTF-8 bytes. The planning oracle gives makespan 8 and C slack 1; the ledger oracle
contains 280 rows and 1,396 total units. Each four-point score is exact/type-aware.
Strict JSON is required. Gemma wrapped all three answers in Markdown fences and
therefore scored zero under the original rule. Removing just the fences as a
**post-hoc explanation**, not replacement scoring, gives 4/4, 3/4 and 3/4. Its
planning slack and context total were also substantively wrong.

The timer spans one native Hermes worker subprocess through decoded response,
including Python/framework/provider startup. It excludes scoring and the 15-second
spacing between calls. It is **not Cloud Run HTTP, Tailscale or Telegram latency**.
The two models were deliberately forced for fair paired prompts; natural policy
choices were recorded separately. These were not six automatic-route observations.
No cross-model fallback occurred. One observation per cell cannot support p95,
confidence intervals, universal speedups or a local-model latency advantage.

Hardware: Apple M4, 16 GiB, arm64 macOS, Python 3.14.4. Hermes pin:
`b200f5bc60e31588ba2cb60e288029c4a54ba5fa`. Ollama was the local backend; served
context was 65,536 tokens. Captured Hermes cloud context budget was 272,000.
Recorded reasoning effort was medium; the local max_tokens field was 2,048;
cloud output cap was absent from capture. Do not infer undisclosed model defaults.
Pre-run local residency was empty; later cache/loading state was uncontrolled.
Quantization, power draw, offload and whole-run memory peaks were not measured.

Hermes-normalized SDK usage: local totals 10,225 input + 2,799 completion tokens;
cloud totals 8,968 input + 275 output. Raw normalized usage fields are retained.
Local hidden-generation split is unavailable. Cloud reasoning counters are
subfields, not additional tokens. Different tokenizers mean counts are not equal
work. Backend/model identity comes from execution metadata, not an output label.

Actual ChatGPT OAuth cost is **unknown**, not zero. No invoice was captured, and
API-key pricing is not the OAuth bill. No current pricing claim is made here.
Local inference uses purchased hardware, electricity and memory; no power/cost
measurements support dollar estimates. Model availability and entitlements vary.
The context benchmark contains public synthetic data, not a private-labelled task
forced to cloud. Separate deterministic tests cover private-local/held behavior.

## Collect independent data

`python -m benchmarks.collect` prints a no-network plan. New live runs require a
separate pinned Hermes checkout and **new** `secrets/benchmark-hermes` profile,
plus `--execute --confirm-independent-profile-and-inference-costs`. That portable
collector times actual backend execution, performs at most three local and three
cloud turns, makes no fallback calls, and stores only synthetic results in ignored
`reports/local/`. It does not instrument SDK token usage, which stays unknown.
Do not run it against production or while preserving an operational window.
Neither it nor live inference was executed for this portfolio preparation.
