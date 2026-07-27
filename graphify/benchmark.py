"""Token-reduction benchmark - measures how much context graphify saves vs naive full-corpus approach."""
from __future__ import annotations
import json
import sys
from pathlib import Path
import networkx as nx
from networkx.readwrite import json_graph

from graphify.paths import default_graph_json as _default_graph_json


# Matches serve._subgraph_to_text's char budget (token_budget * 3) so the
# benchmark and the production renderer estimate tokens the same way.
_CHARS_PER_TOKEN = 3


def _safe(unicode_char: str, ascii_fallback: str) -> str:
    """Return unicode_char if stdout can encode it, else ascii_fallback.

    Windows consoles often default to cp1252 which cannot encode box-drawing
    or arrow glyphs; printing them raises UnicodeEncodeError mid-output.
    """
    encoding = getattr(sys.stdout, "encoding", None) or ""
    try:
        unicode_char.encode(encoding)
        return unicode_char
    except (UnicodeEncodeError, LookupError):
        return ascii_fallback


def _hr(width: int = 50) -> str:
    """Horizontal rule that survives non-UTF-8 stdout (e.g. Windows cp1252 console)."""
    return _safe("─", "-") * width


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _query_subgraph_tokens(G: nx.Graph, question: str, depth: int = 3) -> int:
    """Estimated tokens of the PRODUCTION query output for this question.

    Routes through serve._query_graph_text — the same scoring (_score_query:
    IDF + exact/prefix/substring tiers), seed selection, context filters and
    budget rendering the MCP server and CLI use — so the benchmark measures
    the path users actually pay for, not a naive substring approximation.
    An effectively-unbounded token_budget keeps the measurement about subgraph
    size rather than the render cap.
    """
    from graphify.serve import _query_graph_text

    result = _query_graph_text(G, question, depth=depth, token_budget=1_000_000)
    if result.startswith("No matching nodes"):
        return 0
    return _estimate_tokens(result)


_SAMPLE_QUESTIONS = [
    "how does authentication work",
    "what is the main entry point",
    "how are errors handled",
    "what connects the data layer to the api",
    "what are the core abstractions",
]


def run_benchmark(
    graph_path: str | None = None,
    corpus_words: int | None = None,
    questions: list[str] | None = None,
) -> dict:
    """Measure token reduction: corpus tokens vs graphify query tokens.

    Args:
        graph_path: path to the built graph
        corpus_words: total word count from detect() output; if None, estimated from graph
        questions: list of questions to benchmark; defaults to _SAMPLE_QUESTIONS

    Returns dict with: corpus_tokens, avg_query_tokens, reduction_ratio, per_question
    """
    graph_path = graph_path or _default_graph_json()
    from graphify.security import check_graph_file_size_cap
    check_graph_file_size_cap(Path(graph_path))
    # Load through serve._load_graph — the same loader the query path and the
    # MCP server use. The previous inline node_link_graph(edges="links") assumed
    # a clustered graph and raised KeyError (not TypeError, so the fallback never
    # caught it) on raw `extract --no-cluster` output, which has no "links" key.
    # Sharing the loader also means the benchmark measures the graph exactly as
    # queries see it, including the learning overlay.
    from graphify.serve import _load_graph
    G = _load_graph(str(graph_path))

    if corpus_words is None:
        # Rough estimate: each node label is ~3 words, plus source context
        corpus_words = G.number_of_nodes() * 50

    corpus_tokens = corpus_words * 100 // 75  # words → tokens (100 words ≈ 133 tokens)

    qs = questions or _SAMPLE_QUESTIONS
    per_question = []
    for q in qs:
        qt = _query_subgraph_tokens(G, q)
        if qt > 0:
            per_question.append({"question": q, "query_tokens": qt, "reduction": round(corpus_tokens / qt, 1)})

    if not per_question:
        return {"error": "No matching nodes found for sample questions. Build the graph first."}

    avg_query_tokens = sum(p["query_tokens"] for p in per_question) // len(per_question)
    reduction_ratio = round(corpus_tokens / avg_query_tokens, 1) if avg_query_tokens > 0 else 0

    return {
        "corpus_tokens": corpus_tokens,
        "corpus_words": corpus_words,
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "avg_query_tokens": avg_query_tokens,
        "reduction_ratio": reduction_ratio,
        "per_question": per_question,
    }


def print_benchmark(result: dict) -> None:
    """Print a human-readable benchmark report."""
    if "error" in result:
        print(f"Benchmark error: {result['error']}")
        return

    print(f"\ngraphify token reduction benchmark")
    print(_hr(50))
    arrow = _safe("→", "->")
    print(f"  Corpus:          {result['corpus_words']:,} words {arrow} ~{result['corpus_tokens']:,} tokens (naive)")
    print(f"  Graph:           {result['nodes']:,} nodes, {result['edges']:,} edges")
    print(f"  Avg query cost:  ~{result['avg_query_tokens']:,} tokens")
    print(f"  Reduction:       {result['reduction_ratio']}x fewer tokens per query")
    print(f"\n  Per question:")
    for p in result["per_question"]:
        print(f"    [{p['reduction']}x] {p['question'][:55]}")
    print()
