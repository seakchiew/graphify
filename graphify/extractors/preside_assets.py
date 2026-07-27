"""Preside framework asset extractors: form XML, i18n .properties, webflow YAML.

These three file classes carry most of a Preside application's *declarative*
semantics — the layers the CFML extractor cannot see from code alone:

- ``forms/**/*.xml``  — admin/site form definitions; their ``object=`` /
  ``binding=`` attributes are the form → data-layer wiring, and their file
  paths participate in the project → extension override chain (full-replacement
  semantics; ``preside_resolution._CONVENTION_RE`` already covers kind
  ``forms`` once the files exist in the graph).
- ``i18n/**/*.properties`` — the label layer (the largest text class after
  CFML in a typical project). One node per FILE with key metadata as
  attributes; per-key nodes would explode the node budget (a 2,000-file i18n
  layer must stay ~2,000 nodes, not 100k).
- ``workflow/webflows/*.yml`` — CfFlow webflow definitions; their
  ``event: a.b.c.action`` strings are convention references to handler
  actions, resolved by ``preside_resolution.resolve_cfml_framework``.

Path predicates live in ``graphify.detect`` (which owns classification);
dispatch happens in ``extract._get_extractor`` by path, before generic suffix
dispatch — same pattern as ``.blade.php`` and package manifests.

The webflow parser is deliberately a line-level state machine, not PyYAML:
the tool venv does not ship yaml, the shapes used by webflow files are flat,
and a regex parse cannot be broken by custom tags or anchors.
"""
from __future__ import annotations

import re
from pathlib import Path

from graphify.extractors.base import _file_stem, _make_id, _read_text  # noqa: F401 (_read_text kept for parity)

_XML_SIZE_CAP = 2 * 1024 * 1024  # forms are small; anything bigger is not a form
_MAX_BINDING_EDGES = 30   # distinct preside-object bindings per form
_MAX_I18N_EDGES = 20      # distinct i18n uri prefixes per form
_I18N_KEYS_SAMPLE = 12    # keys stored on a .properties node

_ATTR_OBJECT_RE = re.compile(r"""\bobject\s*=\s*["']([\w-]+)["']""")
_ATTR_BINDING_RE = re.compile(r"""\bbinding\s*=\s*["']([\w-]+)\.[\w-]+["']""")
_ATTR_I18N_RE = re.compile(r"""\b(?:label|placeholder|title|hint|help)\s*=\s*["']([\w.-]+):[\w.-]+["']""")
_FORMS_PO_PATH_RE = re.compile(r"/forms/preside-objects/([\w-]+)/", re.IGNORECASE)

_WEBFLOW_ID_RE = re.compile(r"^\s*id:\s*([\w-]+)\s*$")
_WEBFLOW_STEP_RE = re.compile(r"^\s*-\s+id:\s*([\w-]+)\s*$")
_WEBFLOW_EVENT_RE = re.compile(r"^\s*event:\s*([\w.-]+)\s*$")
_WEBFLOW_FORM_RE = re.compile(r"^\s*form:\s*([\w.-]+)\s*$")
_WEBFLOW_REF_RE = re.compile(r"^\s*-\s+\$(?:subflowref|ref):\s*([\w-]+)\s*$")


def _new_result(path: Path):
    """(nodes, edges, str_path, file_nid) with the file node pre-added."""
    str_path = str(path)
    file_nid = _make_id(str_path)
    nodes: list[dict] = [{
        "id": file_nid,
        "label": path.name,
        "file_type": "code",
        "source_file": str_path,
        "source_location": "L1",
    }]
    return nodes, [], str_path, file_nid


def _add_stub(nodes: list[dict], seen: set[str], name: str, origin: str, **extra) -> str:
    """Sourceless ``type=module`` stub — same shared-anchor idiom as cfml.py
    (#1327): the same preside object / i18n bundle / webflow referenced from N
    files must stay one node, exempt from per-file id disambiguation."""
    nid = _make_id(name)
    if nid not in seen:
        seen.add(nid)
        node = {
            "id": nid,
            "label": name,
            "file_type": "code",
            "type": "module",
            "source_file": "",
            "source_location": "",
            "origin_file": origin,
        }
        node.update(extra)
        nodes.append(node)
    return nid


def _edge(src: str, tgt: str, relation: str, context: str, str_path: str, line: int = 1) -> dict:
    return {
        "source": src,
        "target": tgt,
        "relation": relation,
        "context": context,
        "confidence": "EXTRACTED",
        "source_file": str_path,
        "source_location": f"L{line}",
        "weight": 1.0,
    }


# ── forms/**/*.xml ────────────────────────────────────────────────────────────

def extract_preside_form(path: Path) -> dict:
    """Form definition → file node + binding/i18n edges.

    Attribute mining is regex-based rather than ElementTree: it survives the
    occasional undeclared entity in hand-edited form XML, and the three
    attributes we need (``object=``, ``binding=``, i18n URIs) are flat."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        return {"nodes": [], "edges": [], "error": str(e)}
    if len(raw) > _XML_SIZE_CAP:
        return {"nodes": [], "edges": [], "error": "form xml exceeds size cap"}
    text = raw.decode("utf-8", errors="replace")

    nodes, edges, str_path, file_nid = _new_result(path)
    seen: set[str] = {file_nid}

    objects: list[str] = []
    m = _FORMS_PO_PATH_RE.search(str_path.replace("\\", "/"))
    if m:  # forms/preside-objects/<object>/... — the form's own datasource
        objects.append(m.group(1).lower())
    objects.extend(o.lower() for o in _ATTR_OBJECT_RE.findall(text))
    objects.extend(b.lower() for b in _ATTR_BINDING_RE.findall(text))
    seen_objects: list[str] = []
    for obj in objects:
        if obj not in seen_objects:
            seen_objects.append(obj)
    for obj in seen_objects[:_MAX_BINDING_EDGES]:
        stub = _add_stub(nodes, seen, f"preside-object:{obj}", str_path)
        edges.append(_edge(file_nid, stub, "uses", "form_binding", str_path))

    prefixes: list[str] = []
    for uri_prefix in _ATTR_I18N_RE.findall(text):
        p = uri_prefix.lower()
        if p not in prefixes and p != "cms":  # cms: is Preside core's own bundle
            prefixes.append(p)
    for p in prefixes[:_MAX_I18N_EDGES]:
        stub = _add_stub(nodes, seen, f"i18n:{p}", str_path)
        edges.append(_edge(file_nid, stub, "uses", "i18n_uri", str_path))

    nodes[0]["form_tabs"] = text.count("<tab")
    nodes[0]["form_fieldsets"] = text.count("<fieldset")
    nodes[0]["form_fields"] = text.count("<field ")
    return {"nodes": nodes, "edges": edges}


# ── i18n/**/*.properties ─────────────────────────────────────────────────────

def extract_properties(path: Path) -> dict:
    """i18n bundle → one file node carrying key metadata (never per-key nodes)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    keys: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "!")) or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0].strip())

    nodes, edges, _sp, _fn = _new_result(path)
    nodes[0]["i18n_key_count"] = len(keys)
    nodes[0]["i18n_keys_sample"] = keys[:_I18N_KEYS_SAMPLE]
    return {"nodes": nodes, "edges": edges}


# ── workflow/webflows/*.yml ──────────────────────────────────────────────────

def extract_webflow(path: Path) -> dict:
    """Webflow definition → flow node, step nodes, handler-event references.

    Line-level state machine: tracks the current ``- id:`` step so each
    ``event:`` binds to the step that declares it (flow-level events — e.g.
    postCancelHandler — bind to the flow node)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    nodes, edges, str_path, file_nid = _new_result(path)
    seen: set[str] = {file_nid}
    stem = _file_stem(path)

    flow_id = path.stem
    for line in text.splitlines():
        m = _WEBFLOW_ID_RE.match(line)
        if m:
            flow_id = m.group(1)
            break

    flow_nid = _make_id(stem, flow_id)
    nodes.append({
        "id": flow_nid,
        "label": f"webflow:{flow_id}",
        "file_type": "code",
        "source_file": str_path,
        "source_location": "L1",
    })
    seen.add(flow_nid)
    edges.append(_edge(file_nid, flow_nid, "contains", "webflow", str_path))

    current_owner = flow_nid
    seen_event_pairs: set[tuple[str, str]] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        sm = _WEBFLOW_STEP_RE.match(line)
        if sm:
            step_id = sm.group(1)
            step_nid = _make_id(stem, flow_id, "step", step_id)
            if step_nid not in seen:
                seen.add(step_nid)
                nodes.append({
                    "id": step_nid,
                    "label": f"{flow_id}.{step_id}",
                    "file_type": "code",
                    "source_file": str_path,
                    "source_location": f"L{lineno}",
                })
                edges.append(_edge(flow_nid, step_nid, "contains", "webflow_step", str_path, lineno))
            current_owner = step_nid
            continue
        rm = _WEBFLOW_REF_RE.match(line)
        if rm:  # $ref / $subflowref — reuse of another flow's step or subflow
            stub = _add_stub(nodes, seen, f"webflow-ref:{rm.group(1)}", str_path)
            edges.append(_edge(flow_nid, stub, "references", "webflow_ref", str_path, lineno))
            current_owner = flow_nid
            continue
        fm = _WEBFLOW_FORM_RE.match(line)
        if fm:
            # `form: webflow.<flow>.<step>` → forms/webflow/<flow>/<step>.xml
            # (resolved in preside_resolution); this is the step's own form
            # definition — the structural spine of a join/application journey.
            form_path = fm.group(1)
            if "." in form_path:
                stub = _add_stub(nodes, seen, f"webflow-form:{form_path}", str_path,
                                 cfml_form_path=form_path)
                edges.append(_edge(current_owner, stub, "uses", "webflow_form", str_path, lineno))
            continue
        em = _WEBFLOW_EVENT_RE.match(line)
        if em:
            event = em.group(1)
            if "." not in event:
                continue
            pair = (current_owner, event)
            if pair in seen_event_pairs:
                continue
            seen_event_pairs.add(pair)
            stub = _add_stub(nodes, seen, f"webflow-event:{event}", str_path,
                             cfml_event_path=event)
            edges.append(_edge(current_owner, stub, "references", "webflow_event", str_path, lineno))

    return {"nodes": nodes, "edges": edges}
