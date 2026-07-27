# CFML/Preside benchmarks — four corpora

Measured value of the CFML/Preside graph across four production Preside/ColdBox codebases
of deliberately different shape. Per-project detail lives in each repo's
`.docs/graph-benchmark.md`; this page is the cross-corpus result.

## Method

For each corpus: an agent scanned the code and produced natural-language questions
(**no filenames in the question text**) with verified ground-truth answers. Each question
was then answered twice —

- **BEFORE**: an isolated agent using only Grep/Glob/Read, with the graph forbidden.
  The token figure is that agent's real measured usage, not an estimate.
- **AFTER**: a graph query, plus a vocabulary grep and 2–3 ranged reads to verify the
  cited lines. Query cost measured; the verification allowance (~1,700) is reasoned.

Every BEFORE answer was correct, so the baseline is the cost of *getting it right the
slow way* — not the cost of failing.

## Results

| Corpus | Shape | Project-owned | BEFORE / question | AFTER | Ratio |
|---|---|---:|---:|---:|---:|
| Business application | app-heavy | 17.2% | 120,043 | ~3,518 | **34×** |
| Client portal | lean app | 11.2% | 125,366 | ~3,511 | **36×** |
| Membership site | RM site | 6.6% | 119,504 | ~3,863 | **31×** |
| Membership base | thin RM | 2.7% | 120,207 | ~3,336 | **36×** |

**The baseline is a constant.** Across 22 measured runs the cost never left
111k–130k (σ ≈ 5k), and it does not correlate with question difficulty — a 4-tool
question cost about the same as a 14-tool one. The floor is context setup plus reading
whole files to find one function, and **nothing carries between questions**: every run
rediscovers which of 17–73 extensions owns a behaviour.

Graph build cost is a one-off 6–29s with **zero tokens** (`--code-only`, pure AST).

## Retrieval accuracy tracks the codebase, not the tool

| Corpus | Project-owned | Raw hit@k | Vocab-expanded |
|---|---:|---:|---:|
| Business application | 17.2% | **100%** | 100% |
| Client portal | 11.2% | **83%** | 100% |
| Membership site | 6.6% | **50%** | 100% |
| Membership base | 2.7% | **33%** | 100% |

Perfect rank-order with project-owned share. **This was predicted before measuring** on
the last two corpora and held both times.

Two mechanisms, neither of them the graph:

1. **Signal-to-noise.** A natural-language question competes against every installed
   extension node for the same terms. At 2.7% owned there are 22,299 extension nodes and
   651 project nodes; at 17.2% the ratio is 4:1.
2. **Naming alignment.** Corpora that name things the way people ask about them retrieve
   well raw — "project template" → `ProjectTemplateService`, "profit" →
   `calculateProjectsProfit`. Where they diverge, raw retrieval fails: "job advert" →
   `job_listing`, "nightly job" → `BypassLoginIpService`, "what stops one client
   downloading another's attachments" → `hasAccessToJiraProject`.

### Operational consequence

**Vocabulary expansion is load-bearing, not polish.** It takes raw 33–83% to a uniform
100%. On a thin-project-layer site, skipping it means finding the answer one time in
three.

But it is **not monotonic**: expansion *replaces* the query, so a poor token choice can
discard signal the raw question had. One measured case lost a hit that the raw query
found, and two better token sets recovered it. Expand when the raw query returns nothing
relevant; keep the raw result when the expansion looks worse.

## Layer weighting, isolated

Scoring multiplies node scores by resolution layer (project override > project extension
> installed extension > core), mirroring Preside's own resolution order.

| Corpus | weighting ON | OFF |
|---|---:|---:|
| Business application | **100%** | 88% |
| Membership site | **50%** | 38% |

A consistent **+12 percentage points**. It matters because the file a developer must
actually edit is the project override, and installed extensions outnumber project code
between 4:1 and 34:1 — unweighted, retrieval surfaces the file you must *not* touch.

## Honest limits

- The AFTER figure's verification component (~1,700 tokens) is reasoned, not measured.
  Query cost (1,636–2,163) is exact.
- `graphify benchmark` reports 121–191× on these corpora. That compares a query subgraph
  against dumping the whole codebase, which nobody does. **31–36× is the real number.**
- Accuracy was equal, not better: the graph answered the same questions correctly as
  careful reading did, ~34× cheaper. What changes is what becomes affordable — at 120k a
  question you ask three before compacting; at 3.5k you can ask thirty.
- The baseline agents repeatedly volunteered real defects (mass-assignment surfaces,
  missing transactions, a `catch(Any)` with a too-narrow recovery branch). Graph
  retrieval does not replace reading code; it makes the reading targeted. That is why
  the verify-the-cited-lines step is mandatory rather than optional.
