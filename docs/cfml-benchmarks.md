# CFML/Preside benchmarks — seven corpora

Measured value of the CFML/Preside graph across seven production Preside/ColdBox
codebases of deliberately different shape and age. Per-project detail lives in
each repo's `.docs/graph-benchmark.md`; this page is the cross-corpus result.

> **This page supersedes an earlier four-corpus version.** Two of its
> conclusions did not survive the corpora added since, and a scope defect found
> while adding them. Both corrections are called out below rather than quietly
> edited away. The seventh corpus (prii) confirmed both corrections rather than
> adding new ones.

A rendered, self-contained `graphify-out/graph.html` now exists for all of them —
see "Viewing a graph" at the end.

**Five further projects have since been scope-validated but not token-benchmarked**
(nasc, staffcc, scc, iom3, istructe). They are listed at the end under
"Scope-validated corpora"; the token and hit@k tables below cover the seven
measured ones only, and no figure in them has been extrapolated to the other
five.

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
| servicedesk | lean client portal | 14,143 | 31,550 | 24MB | 5.1% |
| a-n | ReadyMembership site | 44,665 | 85,723 | 68MB | 2.8% |
| msi | thin ReadyMembership base | 36,306 | 73,049 | 55MB | 1.8% |
| prii | thinnest project layer measured | 39,702 | 76,749 | 62MB | 1.0%\* |

\* 1.4% counting the four project-authored extensions that sit inside
`application/extensions/` rather than `extensions_app/` — see "A limit on what
the layer split means".

Build cost is a one-off with **zero tokens** (`--code-only`, pure AST): 10s for
cbi, 90s for inteleos, the largest.

## Token results

| Corpus | BEFORE / question | AFTER | Ratio |
|---|---:|---:|---:|
| cbi | 143,204 | ~3,602 | **40×** |
| prii | 142,905 | ~3,838 | **37×** |
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
breaks that; prii (143k, cheapest run 132k) confirms it — all three of its
baseline runs sit above the old band's ceiling. The range is now
**116k–160k per question**, and within the new
corpora it tracks tool calls closely — cbi's 8-call question cost 116k, its
27-call question 160k.

What was right: there is a **high floor** (~116k) that does not go away, because
every run pays for context setup and for reading whole files to find one
function, and **nothing carries between questions** — each run rediscovers which
of 17–77 extensions owns a behaviour. What was wrong was calling it flat. It is
a floor with a slope.

Note the slope is not codebase size: cbi has the *smallest* graph of the seven and
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
| prii | 1.0% | **33%** | 100% |

### Correction 2 — project-owned share does not predict raw hit@k

The four-corpus version reported a perfect rank-order correlation between
project-owned share and raw hit@k, and noted it had been predicted in advance on
the last two corpora. That correlation does not survive:

- **cbi has the highest project-owned share of all seven (34.2%) and lands
  mid-table at 67%.**
- servicedesk (5.1%) beats inteleos (8.1%) by 50 points.

Part of the reason the old correlation looked so clean is that it was computed
on **incompletely scoped graphs** (see below), so the shares themselves were
wrong. Recomputed on correct scope, the ordering falls apart.

prii is the cleanest test of the retraction, and a discipline lesson. It was
benchmarked with a prediction recorded in advance — "thinnest project layer of
all seven, so raw hit@k below msi's 33%" — derived from the correlation that had
**already been retracted**. It tied msi at 33% instead. Consistent with the
retraction, not with the prediction. Writing a correction down does not stop you
reasoning from the superseded model an hour later; the guard is to re-read the
conclusion before predicting from it.

Of the two mechanisms originally proposed, signal-to-noise and naming alignment,
the evidence now points almost entirely at **naming alignment**. Every raw miss
across the seven is a vocabulary miss — the words a person uses share no stem with
the symbol:

- "urls … are not pages in the site tree" → `MyQualificationRouteHandler`
- "how does that change get back into the crm" → `processOutQueue`
- "what stops one client downloading another's attachments" → `hasAccessToJiraProject`
- "job advert" → `job_listing`; "nightly job" → `BypassLoginIpService`

Signal-to-noise is real but second-order: it decides how badly a weak match is
buried, not whether a match exists.

### Operational consequence

**Vocabulary expansion is load-bearing, not polish.** It takes raw 33–88% to a
uniform 100% on all seven corpora. On a thin-project-layer site, skipping it means
finding the answer one time in three.

It is **not monotonic**: expansion *replaces* the query, so a poor token choice
can discard signal the raw question had. One measured case lost a hit the raw
query found, and two better token sets recovered it. Expand when the raw query
returns nothing relevant; keep the raw result when the expansion looks worse.

One expansion in seven needed the **symbol name itself** rather than domain words
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
- a `!` re-include in `.graphifyignore`, which is what all seven corpora now use

**Prefer the negation.** It is persistent, so it cannot be forgotten on a later
rebuild; it is selective, so `.local/` and build output stay excluded; and it
documents itself in the repo. Forgetting the flag is a silent 40% scope loss
with no warning.

Three traps when writing it:

1. **The negation must live in the same directory as the `.gitignore` rule it
   overrides.** graphify merges the two per directory with `.graphifyignore`
   last, so last match wins *within a directory*; a rule at the repo root loses
   to a `website/.gitignore` rule. Across the seven the blocking rule lived in
   three different places (repo root, `website/`,
   `website/application/extensions/`).
2. **A bare directory rule (`application/extensions`) excludes the directory
   itself**, so `!/application/extensions/**` cannot reach inside it — the
   directory has to be re-included first.
3. **The rule may be prefix-scoped, so probing one directory is not enough.**
   prii's is `application/extensions/preside-ext-*` plus
   `!application/extensions/ext-*/` — it excludes 64 of 68 modules while leaving
   the four project-authored ones tracked. A probe against the first directory
   alphabetically (`data-migration`) reports "not ignored", and the build
   silently produces 854 nodes instead of 39,702. Probe **every** directory:

   ```bash
   for d in website/application/extensions/*/; do git check-ignore -v "$d"; done | sort -u
   ```

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

**Directory location is not a reliable tier signal either**, and this is now
confirmed on two of the seven. inteleos's `inteleos-crm-dynamic` (1,032 tracked
files) and prii's `data-migration`, `ext-finance-report-export`,
`ext-multi-address` and `ext-webflow-extend` are all project-authored code living
inside `application/extensions/` alongside the OOB modules. They are tagged
`extension` and therefore *under*-weighted in retrieval — the opposite of what is
wanted. On prii that is 194 nodes, enough to move project-owned share from 1.0%
to 1.4%.

The signal that actually separates them is **git-tracked-ness**: OOB extensions
are gitignored and reinstalled by `box install`, project-authored ones are
committed. That is not currently used by the layer tagger, and it is the obvious
next improvement.

## Viewing a graph

```bash
graphify cluster-only . --no-label     # add labels with an LLM by dropping --no-label
open graphify-out/graph.html
```

`graph.html` is fully self-contained — the vis-network library is inlined, so it
works offline and inside CSP-restricted viewers with no CDN fetch.

Every real corpus here (8k–50k nodes) exceeds the 5,000-node force-directed cap,
so all seven render as the **aggregated community view**: one node per community,
sized by member count, edges weighted by cross-community link count. Searching,
per-community filtering and click-to-inspect all work on it.

This did not work until recently. The over-cap fallback was gated on graph.json
*byte* size while the thing that breaks the view is *node* count, and the two
diverge — a code-only graph is node-heavy and byte-light. Every corpus took the
raise-an-error branch instead, so `cluster-only` had never produced a viewable
graph on any of them; the reported symptom was "the graph doesn't render" and the
cause was that it was never written. Fixed by passing the effective node limit at
both call sites.

Rendered sizes: cbi 1.3MB (1,184 community nodes) · servicedesk 1.4MB (982) ·
mis 2.9MB (3,568) · msi 3.0MB (3,991) · prii 3.1MB (3,888) · a-n 3.4MB (4,335) ·
inteleos 3.6MB (4,853).

## Scope-validated corpora

Five projects onboarded cold with the `pixl8-graph-builder` skill, to test the
*procedure* rather than re-measure tokens. All five passed the eight-check scope
gate. Full write-up:
`~/Projects/skills/docs/ai-engineering/skill-validation-round.md`.

| Corpus | Nodes | Edges | Build | Project-owned | Extensions |
|---|---:|---:|---:|---:|---:|
| nasc | 45,175 | 85,326 | 78s | 2.1% | 78 |
| staffcc | 41,706 | 80,996 | 81s | 2.2% | 74 |
| scc | 34,796 | 67,733 | 69s | 1.8% | 68 |
| istructe | 22,839 | 44,212 | 43s | 5.1% | 40 |
| iom3 | 3,074 | 3,752 | 4s | **76.4%** | 5 |

iom3 is the extreme of the whole set — a Preside application with almost no
platform above it. Project ownership across all twelve now spans **1.4% to
76.4%**, and the build is correct across the entire range.

No BEFORE baselines were run for these five, deliberately: at ~430k tokens each
they would have cost ~2M tokens to confirm a ratio that had already converged.

### What the round found

Four defects, all in the tooling rather than the graphs:

- Two that made the skill unrunnable in Claude Code — `python3` resolving to a
  shell function that swallows the script argument, and `eval_retrieval.py`
  needing graphify's own interpreter. Both fixed by deriving the interpreter
  from the `graphify` shim.
- A hand-written negation on nasc that produced **1,188 nodes instead of
  45,175** — the bare-directory trap, written wrongly by someone with the
  document describing it open. Step 2 of the skill is now a script.
- Two bugs in that script, caught only because it was tested against all twelve
  projects rather than the five: it overwrote a `.graphifyignore` it should have
  appended to, and mis-computed the path prefix when the blocking rule sat at
  the repo root.

**The rule shapes, across all twelve** — five shapes in four locations, which is
why the negation cannot be written from a table by hand:

| Shape | Rule | Location | Projects |
|---|---|---|---|
| bare directory | `application/extensions` | `website/` | nasc, a-n |
| children glob | `application/extensions/*` | `website/` | inteleos, cbi, servicedesk |
| children glob, root | `website/application/extensions/*` | repo root | mis |
| prefix-scoped | `application/extensions/preside-ext-*` | `website/` | staffcc, scc, prii |
| inside extensions | `preside*` / `preside-ext*` | `…/extensions/` | istructe, msi |
| none | — | — | iom3 |

## Honest limits

- The AFTER figure's verification component (~1,700 tokens) is reasoned, not
  measured. Query cost (1,636–2,215) is exact.
- On inteleos, 5 of 6 queries hit the 2,000-token budget and truncated, as did
  all 4 prii expansions — on a 40–50k-node graph the budget, not the graph, is
  the binding constraint. The answer was in the visible set every time, but raise
  `--budget` when a plausible query looks thin.
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
