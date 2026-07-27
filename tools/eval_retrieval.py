#!/usr/bin/env python
"""Retrieval-accuracy eval: hit@k over a project's golden QA set.

The compression benchmark (``graphify benchmark``) measures efficiency, never
correctness. This script measures correctness: for each question in the
project's golden set it runs the PRODUCTION query path
(``graphify.serve._query_graph_text`` — the same scoring, seeding, context
filters and budget rendering the MCP server and CLI use) and checks whether an
expected file or label appears among the returned NODE lines.

Golden set: ``<project>/.graphify-eval.json`` (project root, deliberately
outside graphify-out/ which gets wiped on rebuilds)::

    {"questions": [{"question": "...",
                    "expect_files": ["services/FooService.cfc"],
                    "expect_labels": ["FooService"]}, ...]}

A question is a HIT when any ``expect_files`` fragment appears in a returned
node's source path, or any ``expect_labels`` entry matches a returned node
label (case-insensitive substring both ways). Misses print the top returned
nodes so a file rename is distinguishable from a genuine retrieval failure.

Usage::

    python tools/eval_retrieval.py <project-dir> [--budget 2000] [--depth 3]
                                   [--threshold 0.8] [--eval-file PATH]
                                   [--graph PATH]

Exits non-zero when the aggregate hit rate is below ``--threshold``, so it can
gate: the mandatory query-before-grep PreToolUse nudge keeps its place on a
project only while this passes (see docs/cfml-howto.md and the
graph-engineering playbook).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from graphify.serve import _load_graph, _query_graph_text

_NODE_LINE = re.compile(r"^\s*NODE\s+(?P<label>.*?)\s+\[src=(?P<src>[^\s\]]*)", re.MULTILINE)


def _returned_nodes(result_text: str) -> list[tuple[str, str]]:
    """(label, source_path) pairs from the query output's NODE lines."""
    return [(m.group("label"), m.group("src")) for m in _NODE_LINE.finditer(result_text)]


def _norm(s: str) -> str:
    return s.replace("\\", "/").lower()


def _is_hit(nodes: list[tuple[str, str]], expect_files: list[str], expect_labels: list[str]) -> bool:
    for label, src in nodes:
        nsrc, nlabel = _norm(src), label.lower()
        for frag in expect_files:
            if frag and _norm(frag) in nsrc:
                return True
        for want in expect_labels:
            w = want.lower()
            if w and (w in nlabel or nlabel.rstrip("()") == w):
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", help="project directory containing .graphify-eval.json and graphify-out/")
    ap.add_argument("--eval-file", default=None, help="golden set path (default <project>/.graphify-eval.json)")
    ap.add_argument("--graph", default=None, help="graph.json path (default <project>/graphify-out/graph.json)")
    ap.add_argument("--budget", type=int, default=2000, help="token budget per query (default 2000)")
    ap.add_argument("--depth", type=int, default=3, help="traversal depth (default 3)")
    ap.add_argument("--threshold", type=float, default=0.8, help="minimum aggregate hit rate (default 0.8)")
    ap.add_argument("--verbose", action="store_true", help="print returned nodes for hits too")
    args = ap.parse_args(argv)

    project = Path(args.project).expanduser().resolve()
    eval_path = Path(args.eval_file) if args.eval_file else project / ".graphify-eval.json"
    graph_path = Path(args.graph) if args.graph else project / "graphify-out" / "graph.json"
    for p, what in ((eval_path, "golden set"), (graph_path, "graph")):
        if not p.exists():
            print(f"error: {what} not found at {p}", file=sys.stderr)
            return 2

    golden = json.loads(eval_path.read_text(encoding="utf-8"))
    questions = golden.get("questions", [])
    if not questions:
        print("error: golden set has no questions", file=sys.stderr)
        return 2

    G = _load_graph(str(graph_path))

    hits = 0
    rows: list[tuple[str, str]] = []
    for q in questions:
        question = q.get("question", "")
        result = _query_graph_text(G, question, depth=args.depth, token_budget=args.budget)
        nodes = _returned_nodes(result)
        hit = _is_hit(nodes, q.get("expect_files", []), q.get("expect_labels", []))
        hits += hit
        rows.append(("HIT " if hit else "MISS", question))
        if not hit or args.verbose:
            tag = "hit" if hit else "MISS"
            print(f"--- [{tag}] {question}")
            if not nodes:
                print("    (no nodes returned)")
            for label, src in nodes[:8]:
                print(f"    {label}  [{src}]")

    total = len(questions)
    rate = hits / total
    print()
    print(f"Retrieval eval — {graph_path}")
    print(f"{'':2}{'result':6} question")
    for verdict, question in rows:
        print(f"  {verdict:6} {question}")
    print()
    print(f"hit@k: {hits}/{total} = {rate:.0%}  (threshold {args.threshold:.0%}, budget {args.budget}, depth {args.depth})")
    if rate < args.threshold:
        print("BELOW THRESHOLD — downgrade any mandatory query-first nudge to advisory until fixed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
