# CFML / Preside how-to

Recipes for working a ColdBox/Preside/ReadyMembership codebase through the graph.
Setup and what the extractor emits: [cfml-readme.md](cfml-readme.md).

Every command below assumes you are in a project root with a built `graphify-out/`.

---

## First build, start to finish

```bash
cd ~/Projects/myclient

# 1. scope the scan (see cfml-readme.md for the full template)
cat > .graphifyignore <<'EOF'
website/preside/
static/_core/
website/assets/
website/uploads/
website/logs/
node_modules/
.local/
*.min.js
*.map
website/application/extensions/*/assets/
website/application/extensions/*/static/
website/application/extensions/*/modules/
graphify-out/
EOF

# 2. re-include the installed extensions, which the project gitignores.
#    THIS FILE MUST SIT BESIDE THE .gitignore IT OVERRIDES — graphify merges
#    .gitignore and .graphifyignore per directory, .graphifyignore last, so
#    last match wins WITHIN a directory. A rule at the repo root loses to a
#    website/.gitignore rule. Find the blocking rule first:
#        git check-ignore -v website/application/extensions/preside-ext-*/ | head -1
#    ...then write the negation into that directory. Across six Preside
#    projects the rule lived in three different places.
cat > website/.graphifyignore <<'EOF'
!/application/extensions/**
EOF
#    If the blocking rule names a bare directory (`application/extensions`
#    rather than `application/extensions/*`) it excludes the DIRECTORY, so
#    nothing inside can be re-included until the directory itself is:
#        !/application/extensions/
#        !/application/extensions/**

# 3. build — local, free, no API key
graphify extract . --code-only

# 3. communities (optional but makes the report readable)
graphify cluster-only . --no-viz --no-label

# 4. keep it fresh + let your assistant use it
graphify hook install
graphify claude install
```

Sanity-check the result before trusting it:

```bash
graphify --version
head -40 graphify-out/GRAPH_REPORT.md
```

If the node count looks far too low, the scan almost certainly missed
`application/extensions/`. Check directly rather than guessing:

```bash
graphify query "preside-ext" | head -5     # should cite extension paths
```

`--no-gitignore` on the extract command achieves the same thing in one shot, and
on one project the two agreed to within 1% (43,537 vs 43,231 nodes). Prefer the
`.graphifyignore` negation anyway: it is persistent, so a later rebuild cannot
forget it, and it keeps `.local/` and build output excluded. Omitting the flag
on a rebuild is a silent ~40% scope loss with no warning — it cost four
already-published measurements.

---

## Before writing code

### "Which file do I create, and what do I extend?"

This is the Preside question, and `overrides` + `inherits` answer it directly.

```bash
graphify explain "EmsTicketSettingsService"
```

You get the component, its methods, what it `inherits` (resolved through the dotted
mapping path), what `overrides` it participates in, and what injects it. From that: if
an extension owns the class, your override goes at the mirrored path under
`application/`, extending the extension's dotted path.

Trace a specific chain end to end:

```bash
graphify path "eventBooking" "EmsTicketSettingsService"
```

### "Does this already exist?"

Reuse-before-write, automated. Run this before adding any new service or handler:

```bash
graphify query "existing subscription renewal logic" --budget 1500
```

If the query returns nothing useful, check that your words match the graph's vocabulary
— the matcher is substring + IDF, with no stemming or synonyms. Extract the actual
vocabulary and pick from it:

```bash
python - <<'EOF'
import json, re
from pathlib import Path
data = json.loads(Path("graphify-out/graph.json").read_text())
vocab = set()
for n in data["nodes"]:
    for c in re.findall(r"[^\W\d_]+", n.get("label", "") or ""):
        for p in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+", c) or [c]:
            if 3 <= len(p) <= 30:
                vocab.add(p.lower())
print(" ".join(sorted(vocab)))
EOF
```

### "What does this service depend on?"

`uses(wirebox_inject)` edges are the DI map, and `uses(preside_object)` is the data
layer. Both show up in `explain`, or query the graph directly:

```bash
python - <<'EOF'
import json, collections
from pathlib import Path
g = json.loads(Path("graphify-out/graph.json").read_text())
by_id = {n["id"]: n for n in g["nodes"]}
links = g.get("links", g.get("edges", []))
target = "PaymentOrderService"   # <-- change me

nid = next((n["id"] for n in g["nodes"] if n.get("label") == target), None)
if not nid:
    raise SystemExit(f"no node labelled {target!r}")
for e in links:
    if e["source"] != nid:
        continue
    if e.get("context") in ("wirebox_inject", "preside_object"):
        print(f"{e['context']:16} -> {by_id[e['target']]['label']}")
EOF
```

To see every file touching one preside object (who writes to `crm_subscription`?):

```bash
graphify explain "preside-object:crm_subscription"
```

---

## Before committing

### Blast radius of a change

```bash
graphify affected "ClientSupportHoursService" --depth 2
```

Because the traversal includes `inherits` and `uses`, this catches the case flat grep
misses: **a project override calling `super.someMethod()` on an extension class whose
signature you just changed**. Narrow to one relation when the default is noisy:

```bash
graphify affected "PaymentOrderService" --relation calls --relation inherits --depth 2
```

### Review the delta, not the diff

Feed a reviewer the scoped subgraph for each symbol you touched rather than raw files:

```bash
for sym in $(git diff --cached --name-only | grep '\.cfc$' | xargs -n1 basename | sed 's/\.cfc$//'); do
  echo "### $sym"
  graphify affected "$sym" --depth 2
done
```

### Find the override chain you might be breaking

```bash
python - <<'EOF'
import json
from pathlib import Path
g = json.loads(Path("graphify-out/graph.json").read_text())
by_id = {n["id"]: n for n in g["nodes"]}
for e in g.get("links", g.get("edges", [])):
    if e.get("relation") == "overrides":
        s, t = by_id[e["source"]], by_id[e["target"]]
        print(f"[{e.get('context'):16}] {s.get('source_file')}\n{'':20}overrides {t.get('source_file')}\n")
EOF
```

---

## Keeping the graph current

```bash
graphify update .                                       # incremental, changed files only
graphify update . --force                               # after a refactor that deleted code
graphify extract . --code-only --no-gitignore --force   # full re-scan, ignore the incremental gate
```

The two `--force` flags are not the same. `update --force` writes the graph even when
the rebuild has **fewer** nodes than the last one — you need it right after deleting
code, because the shrink-guard otherwise refuses the write. `extract --force` skips the
incremental manifest gate and cache reads to do a genuine full re-scan; reach for it
when you suspect the cache is stale rather than the code shrank.

The post-commit hook installed by `graphify hook install` runs the incremental update
for you. After `box install` pulls new extension versions, rebuild fully — the extension
tree changed underneath the graph.

---

## Token cost and answer accuracy

Measure both rather than assuming. They are different numbers:

```bash
graphify benchmark                                  # efficiency: subgraph vs corpus tokens
graphify benchmark --questions .graphify-eval.json  # same, on YOUR domain questions
```

The benchmark runs the production query path. On a mid-size Preside project it reports
~60× on generic questions and ~15× on real domain questions — a compression ratio, not a
quality result. Correctness needs a golden set:

```bash
# 10-15 real questions with known answer files, at the project root
# (see .graphify-eval.json format in tools/eval_retrieval.py)
python tools/eval_retrieval.py ~/Projects/myclient        # hit@k, threshold 0.8
```

**The autonomy gate.** The mandatory "query the graph first" `PreToolUse` nudge that
`graphify claude install` sets up must *earn* its place: install it only where the eval
holds **hit@k ≥ 0.8**, and downgrade it to advisory (remove the hook, keep the CLAUDE.md
section) if the score falls below on a re-run. A mandatory nudge pointing at a weak or
half-built graph makes every session worse, not better. Re-run the eval after any
extractor or scoring change — retrieval regressions are invisible until they burn a
session.

Cap output when you only need orientation; raise `--budget` when a subgraph gets
truncated and the answer might be in the cut nodes.

---

## Multiple projects

Each project keeps its own `graphify-out/`. Merge them into one cross-repo graph to find
logic duplicated across clients — the extraction candidates for a shared extension:

```bash
cd ~/Projects/clienta && graphify extract . --code-only --no-gitignore --global --as clienta
cd ~/Projects/clientb && graphify extract . --code-only --no-gitignore --global --as clientb
graphify global list
```

Then query the merged graph at `~/.graphify/global-graph.json`:

```bash
graphify query "invoice generation" --graph ~/.graphify/global-graph.json
```

Same-named services appearing under several repo tags, or `semantically_similar_to`
edges between them, are your duplication signal.

Script the fleet:

```bash
for p in ~/Projects/*/; do
  [ -d "$p/website/application" ] || continue
  ( cd "$p" && cp ~/templates/.graphifyignore . 2>/dev/null
    graphify extract . --code-only --no-gitignore --global --as "$(basename "$p")" ) \
    || echo "FAILED: $p"
done
```

---

## Troubleshooting

**Almost no nodes, or extensions missing.** You omitted `--no-gitignore`.
`application/extensions/` is gitignored in most Preside projects, so the default
gitignore-respecting walk skips the entire extension tree.

**`tree-sitter-cfml not installed`.** The grammar did not resolve in graphify's
environment. `uv tool install --force graphifyy`, then confirm with
`python -c "import tree_sitter_cfml"`.

**A `.cfm` view produced one node.** Expected — a template with no `<cffunction>` and no
`<cfscript>` has no symbols to extract. Check the handler instead.

**`inherits` edge points at a stub with no source file.** The dotted mapping path
resolved to a file that is not on disk (extension not installed, or an unusual mapping).
Run `box install` and rebuild.

**Injected service didn't bind.** Resolution requires exactly one candidate for the
filename. Two same-named services across extensions with no project-level override are
deliberately left unbound rather than guessed.

**A build "does nothing" after deleting code.** Use `graphify update . --force`; see
*Keeping the graph current* above for why the two `--force` flags differ.

**Slow build, or it walks into a database.** Your `.graphifyignore` is missing
something — commonly `.local/` (a live MySQL data directory in some local stacks) or a
generated `output/` tree. The extract output reports the scanned file count and lists
what it skipped; compare that against what you expect:

```bash
graphify extract . --code-only --no-gitignore 2>&1 | grep -E "scanning|found|skipped|not classified"
```

If the count is far higher than your source tree, find the offender by size:

```bash
du -sh */ website/*/ 2>/dev/null | sort -h | tail -10
```
