# CFML/Preside benchmarks — six corpora

Measured value of the CFML/Preside graph across six production Preside/ColdBox
codebases of deliberately different shape and age. Per-project detail lives in
each repo's `.docs/graph-benchmark.md`; this page is the cross-corpus result.

> **This page supersedes an earlier four-corpus version.** Two of its
> conclusions did not survive the last two corpora and a scope defect found
> while adding them. Both corrections are called out below rather than quietly
> edited away.

## Method

For each corpus: an agent scanned the code and produced natural-language
questions (**no filenames in the question text**) with verified ground-truth
answers. Each question was then answered twice —

- **BEFORE**: an isolated agent using only Grep/Glob/Read, with the graph
  forbidden. The token figure is that agent's real measured usage.
- **AFTER**: a graph query, plus a vocabulary grep and 2–3 ranged reads to
  verify the cited lines. Query cost measured; the verification allowance
  (~1,700) is reasoned.

Every BEFORE answer was correct, so the baseline is the cost of *getting it
right the slow way* — not the cost of failing.

## The corpora

| Corpus | Shape | Nodes | Edges | Size | Project-owned |
|---|---|---:|---:|---:|---:|
| cbi | 9-year-old, lightly vendored | 8,358 | 14,047 | 11MB | **34.2%** |
| mis | business application | 43,231 | 87,362 | 68MB | 9.4% |
| inteleos | fully modularised, 108 modules | 49,611 | 98,084 | 78MB | 8.1% |
| servicedesk | lean client portal | 14,196 | 31,616 | 23MB | 5.1% |
| a-n | ReadyMembership site | 44,665 | 85,723 | 68MB | 2.8% |
| msi | thin ReadyMembership base | 36,306 | 73,049 | 55MB | 1.8% |

Build cost is a one-off with **zero tokens** (`--code-only`, pure AST): 10s for
cbi, 90s for inteleos, the largest.

## Token results

| Corpus | BEFORE / question | AFTER | Ratio |
|---|---:|---:|---:|
| cbi | 143,204 | ~3,602 | **40×** |
| inteleos | 138,500 | ~3,719 | **37×** |
| servicedesk | 125,366 | ~3,511 | **36×** |
| msi | 120,207 | ~3,336 | **36×** |
| mis | 120,043 | ~3,518 | **34×** |
| a-n | 119,504 | ~3,863 | **31×** |

**31–40× cheaper, consistently, across every shape and age of codebase tested.**
That is the headline and it has held up under every correction below.

### Correction 1 — the baseline is not a constant

The four-corpus version reported that BEFORE cost "never left 111k–130k" and did
"not correlate with question difficulty". Adding cbi (143k) and inteleos (138k)
breaks that. The range is now **116k–160k per question**, and within the new
corpora it tracks tool calls closely — cbi's 8-call question cost 116k, its
27-call question 160k.

What was right: there is a **high floor** (~116k) that does not go away, because
every run pays for context setup and for reading whole files to find one
function, and **nothing carries between questions** — each run rediscovers which
of 17–77 extensions owns a behaviour. What was wrong was calling it flat. It is
a floor with a slope.

Note the slope is not codebase size: cbi has the *smallest* graph of the six and
the *highest* baseline.

## Retrieval accuracy

| Corpus | Project-owned | Raw hit@k | Vocab-expanded |
|---|---:|---:|---:|
| mis | 9.4% | **88%** | 100% |
| servicedesk | 5.1% | **83%** | 100% |
| cbi | 34.2% | **67%** | 100% |
| a-n | 2.8% | **50%** | 100% |
| inteleos | 8.1% | **33%** | 100% |
| msi | 1.8% | **33%** | 100% |

### Correction 2 — project-owned share does not predict raw hit@k

The four-corpus version reported a perfect rank-order correlation between
project-owned share and raw hit@k, and noted it had been predicted in advance on
the last two corpora. That correlation does not survive:

- **cbi has the highest project-owned share of all six (34.2%) and lands
  mid-table at 67%.**
- servicedesk (5.1%) beats inteleos (8.1%) by 50 points.

Part of the reason the old correlation looked so clean is that it was computed
on **incompletely scoped graphs** (see below), so the shares themselves were
wrong. Recomputed on correct scope, the ordering falls apart.

Of the two mechanisms originally proposed, signal-to-noise and naming alignment,
the evidence now points almost entirely at **naming alignment**. Every raw miss
across the six is a vocabulary miss — the words a person uses share no stem with
the symbol:

- "urls … are not pages in the site tree" → `MyQualificationRouteHandler`
- "how does that change get back into the crm" → `processOutQueue`
- "what stops one client downloading another's attachments" → `hasAccessToJiraProject`
- "job advert" → `job_listing`; "nightly job" → `BypassLoginIpService`

Signal-to-noise is real but second-order: it decides how badly a weak match is
buried, not whether a match exists.

### Operational consequence

**Vocabulary expansion is load-bearing, not polish.** It takes raw 33–88% to a
uniform 100% on all six corpora. On a thin-project-layer site, skipping it means
finding the answer one time in three.

It is **not monotonic**: expansion *replaces* the query, so a poor token choice
can discard signal the raw question had. One measured case lost a hit the raw
query found, and two better token sets recovered it. Expand when the raw query
returns nothing relevant; keep the raw result when the expansion looks worse.

One expansion in six needed the **symbol name itself** rather than domain words
(inteleos's CPD question). That is still the documented workflow — grep the
vocab index, then query — but it means the answer was reachable only via the
vocab file, not by rephrasing.

## The scope defect worth knowing about

Preside projects gitignore their installed extensions (`box install` regenerates
them). graphify honours `.gitignore`, so **a build that does not say otherwise
silently drops the entire OOB layer** — and with it the whole override chain. On
cbi that produced `overrides: 0` while 27 mirrored paths sat on disk.

There are two ways to say otherwise, and they produce near-identical graphs
(measured on mis: 43,537 nodes with the flag, 43,231 with the negation — the
difference is build junk the flag also re-admits):

- `graphify extract . --code-only --no-gitignore`
- a `!` re-include in `.graphifyignore`, which is what all six corpora now use

**Prefer the negation.** It is persistent, so it cannot be forgotten on a later
rebuild; it is selective, so `.local/` and build output stay excluded; and it
documents itself in the repo. Forgetting the flag is a silent 40% scope loss
with no warning.

Two traps when writing it:

1. **The negation must live in the same directory as the `.gitignore` rule it
   overrides.** graphify merges the two per directory with `.graphifyignore`
   last, so last match wins *within a directory*; a rule at the repo root loses
   to a `website/.gitignore` rule. Across the six the blocking rule was in three
   different places (repo root, `website/`, `website/application/extensions/`).
2. **A bare directory rule (`application/extensions`) excludes the directory
   itself**, so `!/application/extensions/**` cannot reach inside it — the
   directory has to be re-included first.

Separately: extensions on Preside core's `legacyExtensionsNowInCore` list are
never loaded but are still installed on disk. Excluding one removed 2,624 nodes
of unrunnable code from inteleos.

**Consequence for the earlier numbers.** The four-corpus graphs were built at
incomplete scope — whatever the immediate cause, a correctly scoped rebuild of
each is substantially larger, so the extension layer was only partly present.
Corrected clean builds are
(mis 25,305 → 43,231 nodes; msi 24,517 → 36,306; a-n 37,287 → 44,665;
servicedesk 6,511 → 14,196). Token ratios are unaffected — queries are
budget-capped — but every node count and project-owned share in the four-corpus
version was wrong, and mis's raw hit@k fell from 100% to 88% once the full
extension set was competing.

## Layer weighting

Scoring multiplies node scores by resolution layer (project override > project
extension > installed extension > core), mirroring what a developer wants to
edit rather than what Preside loads.

Measured on/off: **+12 percentage points** of hit@k on two corpora. It matters
because installed extensions outnumber project code between 3:1 and 55:1 —
unweighted, retrieval surfaces the file you must *not* touch.

`app-extension` is promoted to parity with `app` when it holds more nodes,
because on a fully modularised app the project root is vestigial (inteleos: 38
nodes vs 3,966). hit@k did not move — the remaining misses score near zero
however they are weighted — but **rank improved on all six inteleos questions**
(952→615, 725→548, 287→215, 126→110, 103→31, 50→49). hit@k is a coarse oracle;
it cannot see improvement that does not cross the cutoff.

### A limit on what the layer split means

It is a **retrieval judgement, not Preside's precedence.**
`ExtensionManagerService.listExtensions()` reads `extensions/` and
`extensions_app/` into one flat array; `_sortExtensions()` orders them by
manifest `id` with `dependsOn` promotion and never reads the `isAppLocal` flag
it records. So `extensions_app` is **not** a guaranteed tier above `extensions`.
On inteleos the `dependsOn` branch happens to lift most project modules above
most `preside-ext-*` ones, so the heuristic lands close — by accident. Treat an
`overrides` edge between the two directories as "these shadow each other", not
as a reliable direction.

## Honest limits

- The AFTER figure's verification component (~1,700 tokens) is reasoned, not
  measured. Query cost (1,636–2,215) is exact.
- On inteleos, 5 of 6 queries hit the 2,000-token budget and truncated — on a
  50k-node graph the budget, not the graph, is the binding constraint.
- `graphify benchmark` reports 121–191× on these corpora. That compares a query
  subgraph against dumping the whole codebase, which nobody does. **31–40× is
  the real number.**
- Accuracy was equal, not better: the graph answered the same questions
  correctly as careful reading did, ~35× cheaper. What changes is what becomes
  affordable — at 140k a question you ask two before compacting; at 3.6k you can
  ask thirty.
- The baseline agents repeatedly volunteered real defects (a webhook with no
  HMAC and no ownership check; geocoding failures that permanently strand a
  record; a `bigint` column truncating fractional credits). Graph retrieval does
  not replace reading code; it makes the reading targeted. That is why the
  verify-the-cited-lines step is mandatory rather than optional.
