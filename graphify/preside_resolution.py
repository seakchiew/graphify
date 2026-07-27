"""Preside/ColdBox framework resolution for CFML corpora.

ColdBox (and Preside CMS / ReadyMembership on top of it) is a convention-based
framework: folder structure drives routing, DI and override resolution. The
per-file CFML extractor records the raw framework facts (``cfml_extends``
dotted mapping paths, WireBox ``inject=`` targets, handler actions); this
corpus-level pass turns them into resolved edges:

1. ``extends="app.extensions.preside-ext-x.handlers.y"`` dotted mapping paths
   resolve to the actual CFC file, rewiring the ``inherits`` stub the extractor
   emitted onto the real component node.
2. ``overrides`` edges between files that share a convention-relative path
   (``handlers/admin/foo.cfc`` in the project vs the same path inside an
   installed extension) — Preside's resolution order is project →
   extension → core, and this chain is the single most important structural
   relation in a Preside codebase.
3. Leftover WireBox ``inject=`` stubs resolve by unique service filename,
   preferring a project-level match over extensions (mirroring WireBox's own
   convention scan order).
4. Member calls through injected services (``jiraSyncService.sync()`` where
   ``property name="jiraSyncService" inject="JiraSyncService"``) bind to the
   target service's method — the ColdBox equivalent of receiver-typed
   member-call resolution.
5. ``super.method()`` calls bind through the (now resolved) inherits chain.
6. Handler actions link to their convention view
   (``handlers/a/b.cfc::action`` → ``views/a/b/action.cfm``) via
   ``references`` edges (context ``renders``).

Registered as a LanguageResolver, same seam as the Ruby/Pascal resolvers.
Every emission requires a single unambiguous candidate (god-node guard) — an
ambiguous name produces no edge rather than a guess.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath


# application/<kind>/... with an optional extension segment in front.
# Two extension roots exist in the wild: `extensions/` (installed by CommandBox,
# gitignored, read-only) and `extensions_app/` (project-authored extensions,
# git-tracked — the client's own code). They occupy different tiers: a file in
# application/<kind>/ overrides one in extensions_app/, which overrides one in
# extensions/, which overrides core.
_CONVENTION_RE = re.compile(
    r"(?:^|/)application/(?:(?P<extroot>extensions|extensions_app)/(?P<ext>[^/]+)/)?"
    r"(?P<kind>handlers|services|views|layouts|preside-objects|forms|i18n|base|interceptors|helpers)/"
    r"(?P<rest>.+)$",
    re.IGNORECASE,
)
_CORE_RE = re.compile(
    r"(?:^|/)preside/system/"
    r"(?P<kind>handlers|services|views|layouts|preside-objects|forms|i18n|base|interceptors|helpers)/"
    r"(?P<rest>.+)$",
    re.IGNORECASE,
)


def _norm(source_file: object) -> str:
    return str(source_file or "").replace("\\", "/")


def _convention_key(source_file: object):
    """(kind, rest_lower, tier, ext_name) for a file under a convention folder.

    Resolution tiers, lowest wins: 0 = project ``application/<kind>/…``,
    1 = ``application/extensions_app/<x>/…`` (project-authored extension),
    2 = ``application/extensions/<x>/…`` (installed), 3 = Preside core.
    None when the file is outside any convention folder.
    """
    path = _norm(source_file)
    m = _CONVENTION_RE.search(path)
    if m:
        if not m.group("ext"):
            tier = 0
        else:
            tier = 1 if m.group("extroot").lower() == "extensions_app" else 2
        return m.group("kind").lower(), m.group("rest").lower(), tier, m.group("ext") or ""
    m = _CORE_RE.search(path)
    if m:
        return m.group("kind").lower(), m.group("rest").lower(), 3, ""
    return None


def _mapping_path_suffixes(dotted: str) -> list[str]:
    """Candidate path suffixes for a ColdBox/Preside dotted mapping path.

    ``app.extensions.preside-ext-x.handlers.y`` → ``application/extensions/
    preside-ext-x/handlers/y.cfc``; ``preside.system.base.AdminHandler`` →
    ``preside/system/base/adminhandler.cfc``. ``app`` maps to the project's
    ``application`` folder. Comparison is lowercased (CFML mappings are
    case-insensitive).
    """
    parts = [p for p in dotted.strip().lower().split(".") if p]
    if not parts:
        return []
    if parts[0] == "app":
        parts = ["application"] + parts[1:]
    return ["/".join(parts) + ".cfc"]


def resolve_cfml_framework(
    per_file: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
) -> None:
    node_by_id = {n.get("id"): n for n in all_nodes}

    # component nodes: targets of `contains` from a .cfc file node AND sources
    # of framework attrs. The extractor stores `cfml_extends` on the component.
    components: list[dict] = [
        n for n in all_nodes
        if _norm(n.get("source_file")).lower().endswith(".cfc")
        and not str(n.get("label", "")).endswith((".cfc", ")"))
    ]
    comp_by_path: dict[str, list[dict]] = {}
    comp_by_stem: dict[str, list[dict]] = {}
    for c in components:
        path = _norm(c.get("source_file")).lower()
        comp_by_path.setdefault(path, []).append(c)
        comp_by_stem.setdefault(PurePosixPath(path).stem, []).append(c)

    # .xml (forms — full-replacement overrides) and .properties (i18n — also
    # full-replacement) participate in the convention chain alongside CFML.
    file_nodes = {
        _norm(n.get("source_file")).lower(): n
        for n in all_nodes
        if str(n.get("label", "")).lower().endswith((".cfc", ".cfm", ".xml", ".properties"))
        and n.get("source_file")
    }

    def _component_for_suffix(suffix: str) -> dict | None:
        hits = [c for path, group in comp_by_path.items() if path.endswith(suffix) for c in group]
        return hits[0] if len(hits) == 1 else None

    # ---- 1. extends dotted-path resolution --------------------------------
    inherits_edges = [e for e in all_edges if e.get("relation") == "inherits"]
    for edge in inherits_edges:
        target = node_by_id.get(edge.get("target"))
        if target is None or target.get("source_file"):
            continue  # already resolved (label rewire or earlier pass)
        source = node_by_id.get(edge.get("source"))
        dotted = (source or {}).get("cfml_extends") or target.get("cfml_extends_path") or ""
        if "." not in dotted:
            continue
        for suffix in _mapping_path_suffixes(dotted):
            comp = _component_for_suffix(suffix)
            if comp is not None:
                edge["target"] = comp["id"]
                break

    # ---- 2. overrides chain ------------------------------------------------
    by_key: dict[tuple[str, str], list[tuple[int, dict]]] = {}
    for path, fnode in file_nodes.items():
        key = _convention_key(path)
        if key is None:
            continue
        kind, rest, tier, _ext = key
        by_key.setdefault((kind, rest), []).append((tier, fnode))

    existing_pairs = {(e.get("source"), e.get("target")) for e in all_edges}

    def _emit(src: dict, tgt: dict, relation: str, context: str,
              confidence: str = "EXTRACTED", score: float = 1.0,
              source_file: str | None = None, location: str | None = None) -> None:
        pair = (src["id"], tgt["id"])
        if pair in existing_pairs or src["id"] == tgt["id"]:
            return
        existing_pairs.add(pair)
        edge = {
            "source": src["id"],
            "target": tgt["id"],
            "relation": relation,
            "context": context,
            "confidence": confidence,
            "confidence_score": score,
            "source_file": source_file or src.get("source_file", ""),
            "source_location": location or src.get("source_location", ""),
            "weight": 1.0,
        }
        all_edges.append(edge)

    for (kind, _rest), group in by_key.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda t: t[0])
        # each lower tier overrides every higher tier with the same path
        for i, (tier_a, node_a) in enumerate(group):
            for tier_b, node_b in group[i + 1:]:
                if tier_a < tier_b:
                    _emit(node_a, node_b, "overrides", kind)

    # ---- 3. leftover inject= stub resolution -------------------------------
    def _resolve_service(name: str) -> dict | None:
        candidates = comp_by_stem.get(name.lower(), [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            # project wins over extensions (WireBox scans the project first)
            project = [c for c in candidates
                       if (_convention_key(c.get("source_file")) or (None, None, 9))[2] == 0]
            if len(project) == 1:
                return project[0]
        return None

    for edge in all_edges:
        if edge.get("context") != "wirebox_inject":
            continue
        target = node_by_id.get(edge.get("target"))
        if target is None or target.get("source_file"):
            continue
        comp = _resolve_service(str(target.get("label", "")))
        if comp is not None:
            edge["target"] = comp["id"]

    # ---- indexes shared by passes 4 + 5 ------------------------------------
    method_index: dict[tuple[str, str], str] = {}
    owner_of: dict[str, str] = {}
    for e in all_edges:
        if e.get("relation") != "method":
            continue
        owner, method_nid = e.get("source"), e.get("target")
        owner_of[method_nid] = owner
        mnode = node_by_id.get(method_nid)
        if mnode is not None:
            mname = str(mnode.get("label", "")).removesuffix("()").lower()
            method_index[(owner, mname)] = method_nid

    bases_of: dict[str, list[str]] = {}
    for e in all_edges:
        if e.get("relation") == "inherits":
            bases_of.setdefault(e["source"], []).append(e["target"])

    def _find_method(owner_nid: str, mname: str, depth: int = 0) -> str | None:
        if depth > 8:
            return None
        hit = method_index.get((owner_nid, mname))
        if hit:
            return hit
        for base in bases_of.get(owner_nid, []):
            hit = _find_method(base, mname, depth + 1)
            if hit:
                return hit
        return None

    # ---- 4 + 5. injected-service member calls and super() calls ------------
    cfml_raw = [
        rc for result in per_file for rc in result.get("raw_calls", [])
        if _norm(rc.get("source_file")).lower().endswith((".cfc", ".cfm"))
    ]
    for rc in cfml_raw:
        caller = rc.get("caller_nid")
        callee = str(rc.get("callee", "")).lower()
        if not caller or not callee or caller not in node_by_id:
            continue
        if rc.get("is_super_call"):
            owner = owner_of.get(caller)
            if not owner:
                continue
            for base in bases_of.get(owner, []):
                method_nid = _find_method(base, callee)
                if method_nid:
                    _emit(node_by_id[caller], node_by_id[method_nid], "calls", "super_call",
                          source_file=rc.get("source_file", ""),
                          location=rc.get("source_location"))
                    break
            continue
        injected = rc.get("injected_target")
        if injected:
            comp = _resolve_service(str(injected).split(":", 1)[-1])
            if comp is None:
                continue
            method_nid = _find_method(comp["id"], callee)
            target = node_by_id.get(method_nid) if method_nid else comp
            if target is not None:
                # inject="X" names the receiver type explicitly in source →
                # EXTRACTED, matching the qualified-call convention (#1533).
                _emit(node_by_id[caller], target,
                      "calls" if method_nid else "references", "injected_call",
                      source_file=rc.get("source_file", ""),
                      location=rc.get("source_location"))

    # ---- 6pre. preside-object stubs ← their defining object CFCs ------------
    # The `preside-object:<name>` stub stays a concept hub (additive merge:
    # project AND extension files both define the object, so binding the stub
    # to one file would misrepresent the semantics). Instead every definer
    # gets a `defines` edge INTO the hub, linking the data-layer map to the
    # actual definitions.
    po_stubs = {
        str(n.get("label", ""))[15:]: n
        for n in all_nodes
        if not n.get("source_file") and str(n.get("label", "")).startswith("preside-object:")
    }
    if po_stubs:
        for path, group in comp_by_path.items():
            key = _convention_key(path)
            if key is None or key[0] != "preside-objects" or len(group) != 1:
                continue
            stem_name = PurePosixPath(path).stem
            stub = po_stubs.get(stem_name)
            if stub is not None:
                _emit(group[0], stub, "defines", "preside_object")

    # ---- 6a. i18n uri stubs → .properties file nodes ------------------------
    # Stub label `i18n:<prefix>` (from translateResource captures and form
    # label attributes); prefix "invoicing" → i18n/invoicing.properties,
    # "preside-objects.server" → i18n/preside-objects/server.properties.
    properties_files = {
        path: fnode for path, fnode in
        ((_norm(n.get("source_file")).lower(), n) for n in all_nodes)
        if path.endswith(".properties")
    }
    if properties_files:
        # Convention linkage: Preside resolves an object's/page-type's labels by
        # path, with no translateResource call to capture — i18n/preside-objects/
        # <obj>.properties (and i18n/page-types/<obj>.properties) IS the label
        # bundle for <obj>. Wire each bundle to the object's defining CFC when
        # that file is in the corpus, else to the concept hub. Without this they
        # are isolated nodes: real files, zero edges, invisible to every query.
        object_cfc_by_stem: dict[str, list[dict]] = {}
        for cpath, group in comp_by_path.items():
            key = _convention_key(cpath)
            if key and key[0] == "preside-objects":
                object_cfc_by_stem.setdefault(PurePosixPath(cpath).stem, []).extend(group)
        for path, fnode in properties_files.items():
            marker = next((m for m in ("/i18n/preside-objects/", "/i18n/page-types/")
                           if m in path), None)
            if marker is None:
                continue
            obj = PurePosixPath(path[path.index(marker) + len(marker):]).stem
            defs = object_cfc_by_stem.get(obj, [])
            if len(defs) == 1:
                _emit(fnode, defs[0], "uses", "i18n_bundle")
            elif po_stubs and obj in po_stubs:
                _emit(fnode, po_stubs[obj], "uses", "i18n_bundle")

        i18n_stubs = {
            n["id"]: str(n.get("label", ""))[5:]
            for n in all_nodes
            if not n.get("source_file") and str(n.get("label", "")).startswith("i18n:")
        }
        for stub_id, prefix in i18n_stubs.items():
            suffix = "/i18n/" + prefix.replace(".", "/").lower() + ".properties"
            hits = [f for path, f in properties_files.items() if path.endswith(suffix)]
            if len(hits) > 1:
                # override chain: project wins (same rule as services)
                project = [f for f in hits
                           if (_convention_key(f.get("source_file")) or (None, None, 9))[2] == 0]
                hits = project if len(project) == 1 else hits
            if len(hits) == 1:
                target_id = hits[0]["id"]
                for edge in all_edges:
                    if edge.get("target") == stub_id:
                        edge["target"] = target_id

    # ---- 6a2. webflow form stubs → forms/<dotted path>.xml ------------------
    # `form: webflow.<flow>.<step>` → forms/webflow/<flow>/<step>.xml. This is
    # the step→form spine of a join/application journey; without it every step
    # form is an isolated node and "what does step X ask for" is unanswerable.
    form_files = {
        path: fnode for path, fnode in file_nodes.items() if path.endswith(".xml")
    }
    if form_files:
        for node in list(all_nodes):
            dotted = node.get("cfml_form_path")
            if not dotted or node.get("source_file"):
                continue
            suffix = "/forms/" + str(dotted).lower().replace(".", "/") + ".xml"
            hits = [f for path, f in form_files.items() if path.endswith(suffix)]
            if len(hits) > 1:
                project = [f for f in hits
                           if (_convention_key(f.get("source_file")) or (None, None, 9))[2] == 0]
                hits = project if len(project) == 1 else hits
            if len(hits) == 1:
                target_id = hits[0]["id"]
                for edge in all_edges:
                    if edge.get("target") == node["id"]:
                        edge["target"] = target_id

    # ---- 6b. webflow event stubs → handler action methods -------------------
    # Stub attr cfml_event_path "admin.webflow.newProjectSetup.standardInfo" →
    # handlers/admin/webflow/newProjectSetup.cfc :: standardInfo(). Convention:
    # last segment is the action, the rest is the handler path.
    handler_files: dict[str, dict] = {}
    for path, group in comp_by_path.items():
        key = _convention_key(path)
        if key and key[0] == "handlers" and len(group) == 1:
            rest = key[1][:-4] if key[1].endswith(".cfc") else key[1]
            handler_files.setdefault(rest, group[0])
    for node in all_nodes:
        event = node.get("cfml_event_path")
        if not event or node.get("source_file"):
            continue
        parts = [p for p in str(event).lower().split(".") if p]
        if len(parts) < 2:
            continue
        comp = handler_files.get("/".join(parts[:-1]))
        if comp is None:
            continue
        method_nid = method_index.get((comp["id"], parts[-1]))
        target_id = method_nid or comp["id"]
        for edge in all_edges:
            if edge.get("target") == node["id"]:
                edge["target"] = target_id

    # ---- 6. handler action → convention view -------------------------------
    view_files = {
        path: fnode for path, fnode in file_nodes.items() if path.endswith(".cfm")
    }
    view_by_suffix: dict[str, list[dict]] = {}
    for path, fnode in view_files.items():
        key = _convention_key(path)
        if key and key[0] == "views":
            view_by_suffix.setdefault(key[1], []).append(fnode)

    for comp in components:
        key = _convention_key(comp.get("source_file"))
        if key is None or key[0] != "handlers":
            continue
        handler_rest = key[1][:-4] if key[1].endswith(".cfc") else key[1]  # drop .cfc
        for (owner, mname), method_nid in method_index.items():
            if owner != comp["id"]:
                continue
            view_rest = f"{handler_rest}/{mname}.cfm"
            hits = view_by_suffix.get(view_rest, [])
            if len(hits) == 1:
                _emit(node_by_id[method_nid], hits[0], "references", "renders",
                      confidence="INFERRED", score=0.85)

    # ---- 7. drop stubs this pass orphaned ----------------------------------
    # Passes 1/3/6a/6b rewire an edge's target from a placeholder stub to the
    # real node. The stub itself then has no edges left and would ship as a
    # dead node — noise in the viz, in god-node ranking, and in every query
    # subgraph that walks near it. Remove only sourceless stubs that ended the
    # pass with zero edges; a stub that never resolved keeps its edges and
    # stays, because "referenced but not in this corpus" is real information
    # (e.g. an RM extension that isn't installed in this checkout).
    referenced: set[str] = set()
    for e in all_edges:
        referenced.add(e.get("source"))
        referenced.add(e.get("target"))
    dead = [
        n for n in all_nodes
        if not n.get("source_file") and n.get("id") not in referenced
        and str(n.get("label", "")).split(":", 1)[0] in
        ("preside-object", "i18n", "webflow-event", "webflow-ref", "webflow-form")
    ]
    if dead:
        dead_ids = {n["id"] for n in dead}
        all_nodes[:] = [n for n in all_nodes if n.get("id") not in dead_ids]
