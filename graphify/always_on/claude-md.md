## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships. Every rule below applies only while `graphify` is on PATH and graphify-out/graph.json exists — otherwise work source-first as normal.

Rules:
- At the start of graph work, run `graphify reflect --if-stale` (cheap, deterministic, no LLM) and read graphify-out/reflections/LESSONS.md when it exists: start from its preferred sources, skip its known dead ends, apply its corrections.
- For codebase questions, first run `graphify query "<question>"`. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- A graph answer is a draft: before relying on it or recording an outcome, read the cited source_location lines and confirm they support the claim (skip only for trivial where-is-X lookups). Record what you observed via `graphify save-result ... --outcome useful|dead_end|corrected` — never mark `useful` unchecked.
- If two query attempts (with a changed strategy between them) both fail, say "inconclusive — answering from source" and fall back to grep/read. No third attempt.
- Before committing a change to a shared symbol, run `graphify affected "<symbol>" --depth 2` in self-review — it traverses calls/inherits/uses, catching callers and overrides flat grep misses.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
