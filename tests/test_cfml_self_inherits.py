"""A CFML component never inherits from itself.

A project class extending the same-named core class that is not in the corpus
(`handlers/General.cfc` extends `preside.system.handlers.General`) leaves a
bare-name stub that resolves back onto the component's own node. The build
keeps pre-existing self-loops, so the Preside resolver has to drop this one.
"""
from __future__ import annotations

import os
from pathlib import Path

from graphify.extract import extract


def _extract(tmp_path, files: dict[str, str]):
    for name, body in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    old = os.getcwd()
    try:
        os.chdir(tmp_path)
        return extract([Path(n) for n in files], cache_root=tmp_path)
    finally:
        os.chdir(old)


def test_extends_same_named_class_outside_corpus_is_not_a_self_loop(tmp_path):
    result = _extract(tmp_path, {
        "application/handlers/General.cfc": (
            'component extends="preside.system.handlers.General" {\n'
            "\tpublic void function index( event, rc, prc ) {\n"
            "\t\tsuper.index( argumentCollection=arguments );\n"
            "\t}\n"
            "}\n"
        ),
        "application/handlers/Other.cfc": (
            'component extends="app.handlers.General" {\n}\n'
        ),
    })
    inherits = [e for e in result["edges"] if e.get("relation") == "inherits"]
    assert not [e for e in inherits if e["source"] == e["target"]]
    # the real in-corpus extends is still resolved
    by_id = {n["id"]: n for n in result["nodes"]}
    assert any(
        by_id[e["source"]].get("label") == "Other"
        and by_id.get(e["target"], {}).get("label") == "General"
        for e in inherits
    )
