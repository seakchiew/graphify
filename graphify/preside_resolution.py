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

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path, PurePosixPath

from graphify.extractors.base import _make_id


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


_LAYER_RULES = (
    # (path marker, layer) — first match wins, checked on the posix-lowered path.
    ("/static/templates/", "static"),
    ("/static/assets/", "static"),
    ("/static/", "static"),
    ("/preside/system/", "core"),
    ("/application/extensions_app/", "app-extension"),
    ("/application/extensions/", "extension"),
    ("/application/", "app"),
)

# `extensions_app/` is the modern home for project-authored modules, but older
# projects put them in `extensions/` alongside the installed ones, so the
# directory alone mis-tags them as OOB — the layer then says "vendor code" about
# code the project owns and can edit.
#
# MEASURED EFFECT: none, so far. Retagging 4,058 nodes across four corpora (mis
# 685, inteleos 2,707, msi 472, prii 194) moved neither hit@k nor the rank of a
# single expected node on 26 eval questions, even where the promotion is 1.00 →
# 1.60. This is kept as a correctness fix — the layer attribute is consumed by
# reports, filters and anything else reading graph.json, and saying "vendor" about
# first-party code is simply wrong — but it should not be described as a
# retrieval improvement without evidence that does not currently exist. The eval
# sets may not pose questions where a project module competes with OOB at the
# margin; that is the experiment still to run.
#
# What separates them is **git**: installed extensions are gitignored and
# restored by `box install`, project-authored ones are committed. Measured on
# seven production projects, this classified every module correctly, including
# the cases a `preside-ext-` naming rule gets wrong in both directions —
# `common-editable-form-content` is unprefixed but box-installed (OOB), and a
# project is free to commit a module under any name it likes.
#
# When git cannot answer — no repo, git missing, or a project that vendors every
# extension so "tracked" stops discriminating — everything under `extensions/`
# stays `extension`, which is the pre-existing behaviour. The signal only ever
# promotes; it never guesses.
_EXTENSIONS_MARKER = "/application/extensions/"


@lru_cache(maxsize=8)
def _tracked_extension_modules(root: str) -> frozenset | None:
    """Names of modules under application/extensions/ that are committed.

    ``None`` when git cannot answer usefully — no repo, git missing, or a
    project that vendors *every* extension.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", root, "ls-files", "--", "*application/extensions/*"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None

    on_disk = set()
    for base in (Path(root) / "website" / "application" / "extensions",
                 Path(root) / "application" / "extensions"):
        if base.is_dir():
            on_disk |= {d.name.lower() for d in base.iterdir() if d.is_dir()}
    if not on_disk:
        return None

    tracked: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split("/")
        try:
            i = parts.index("extensions")
        except ValueError:
            continue
        # Intersecting with the directories actually present drops files
        # committed straight into extensions/ — several projects track a
        # README.md and an extensions.json there, which would otherwise read as
        # module names.
        if i + 1 < len(parts) and parts[i - 1] == "application":
            name = parts[i + 1].lower()
            if name in on_disk:
                tracked.add(name)
    if not tracked or tracked >= on_disk:
        return None  # nothing committed, or everything vendored: no information
    return frozenset(tracked)


def _extension_module_layer(path: str, root: str) -> str:
    """`extension` or `app-extension` for a file under application/extensions/."""
    module = path.split(_EXTENSIONS_MARKER, 1)[1].split("/", 1)[0]
    tracked = _tracked_extension_modules(root) or frozenset()
    return "app-extension" if module in tracked else "extension"


def _layer_of(source_file: object) -> str | None:
    """Which layer of a Preside project a file belongs to.

    A Preside deployment is two applications in one repo: the Preside/RM
    application under ``website/application`` and the Pixl8 Frontend Framework
    v2 build under ``static/`` (its own Application.cfc, box.json, deploy.sh).
    They belong in one graph — they are one project and reference each other —
    but conflating them makes "where is this rendered" ambiguous, so every node
    carries the layer it came from: static | app | app-extension | extension |
    core. Query with it, filter reports by it, and keep frontend conventions
    (templates/pages, templates/modules, widgets, LESS/grunt) reasoned about
    separately from handler/service/preside-object conventions.
    """
    path = _norm(source_file).lower()
    if not path:
        return None
    # Prepend "/" so a repo-relative path ("static/templates/x.cfm") matches the
    # same anchored markers as an absolute one — without it every top-level
    # directory silently failed to classify.
    if not path.startswith("/"):
        path = "/" + path
    for marker, layer in _LAYER_RULES:
        if marker in path:
            if marker == _EXTENSIONS_MARKER:
                return _extension_module_layer(path, os.getcwd())
            return layer
    return None


def resolve_cfml_framework(
    per_file: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
) -> None:
    node_by_id = {n.get("id"): n for n in all_nodes}

    # Tag every sourced node with its layer before anything else, so downstream
    # passes (and every consumer of graph.json) can tell frontend from app.
    for _n in all_nodes:
        _layer = _layer_of(_n.get("source_file"))
        if _layer:
            _n["layer"] = _layer

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

    # ---- 6c. interceptor registrations → the interceptor component ---------
    # `interceptors.append({class="app.interceptors.Foo"})` carries a dotted
    # mapping path, same shape as extends=, so reuse that resolver.
    for node in list(all_nodes):
        dotted = node.get("cfml_interceptor_class")
        if not dotted or node.get("source_file"):
            continue
        for suffix in _mapping_path_suffixes(dotted):
            comp = _component_for_suffix(suffix)
            if comp is not None:
                for edge in all_edges:
                    if edge.get("target") == node["id"]:
                        edge["target"] = comp["id"]
                break

    # ---- 6d. interceptor listener methods → interception points ------------
    # ColdBox binds an interceptor to a point BY METHOD NAME — there is no
    # registration for the binding itself, so without this the announce site
    # and the code that runs in response are unconnected. Only bind methods on
    # components that live in an interceptors/ folder (or are registered as
    # interceptors) whose name matches a point some code actually announces:
    # matching on name alone would bind every same-named method in the corpus.
    point_stub_by_name = {
        str(n.get("label", ""))[len("interception-point:"):].lower(): n
        for n in all_nodes
        if str(n.get("label", "")).startswith("interception-point:")
    }
    registered_ids = {
        e.get("target") for e in all_edges if e.get("context") == "interceptor"
    }
    # ColdBox's binding rule IS the method name: a public method on an
    # interceptor named `preRender` runs when `preRender` is announced. There
    # is no registration for the binding, so every public method here is a
    # listener and gets a hub — created on demand rather than matched against a
    # fixed list, because the announcer is very often OUT of corpus (core
    # ColdBox/Preside announces preRender, preSelectObjectData, onLoginSuccess
    # …, and website/preside/ is not indexed). Matching only already-announced
    # points would silently drop exactly the framework hooks people search for.
    # `configure` is ColdBox's own lifecycle method, and `_`-prefixed methods
    # are private helpers by house convention — neither is a listener.
    for comp in components:
        key = _convention_key(comp.get("source_file"))
        stem = PurePosixPath(_norm(comp.get("source_file")).lower()).stem
        is_interceptor = (key is not None and key[0] == "interceptors") \
            or comp["id"] in registered_ids \
            or stem.endswith("interceptor") or stem.endswith("interceptors")
        if not is_interceptor:
            continue
        for (owner, mname), method_nid in method_index.items():
            if owner != comp["id"] or mname.startswith("_") or mname == "configure":
                continue
            stub = point_stub_by_name.get(mname)
            if stub is None:
                label = str(node_by_id[method_nid].get("label", "")).removesuffix("()")
                stub_id = _make_id(f"interception-point:{label}")
                stub = node_by_id.get(stub_id)
                if stub is None:
                    stub = {
                        "id": stub_id,
                        "label": f"interception-point:{label}",
                        "file_type": "code",
                        "type": "module",
                        "source_file": "",
                        "source_location": "",
                        "origin_file": comp.get("source_file", ""),
                    }
                    all_nodes.append(stub)
                    node_by_id[stub_id] = stub
                point_stub_by_name[mname] = stub
            _emit(node_by_id[method_nid], stub, "listens_to", "interception")

    # ---- 6c2. viewlet / view / runEvent string targets ---------------------
    # `renderViewlet( event="cmsLayout._breadCrumbs" )` names a handler action
    # by dotted convention path — usually a PRIVATE method, which nothing else
    # in the graph references, so without this every viewlet implementation is
    # an orphan. `renderView( view="/cmsLayout/_header" )` names a .cfm under
    # some layer's views/ root. Both resolve through the same project →
    # extension → core search the framework itself performs.
    handler_comp_by_rest: dict[str, list[dict]] = {}
    for path, group in comp_by_path.items():
        key = _convention_key(path)
        if key and key[0] == "handlers":
            rest = key[1][:-4] if key[1].endswith(".cfc") else key[1]
            handler_comp_by_rest.setdefault(rest, []).extend(group)

    view_by_rest: dict[str, list[dict]] = {}
    for path, fnode in file_nodes.items():
        key = _convention_key(path)
        if key and key[0] in ("views", "layouts") and key[1].endswith(".cfm"):
            view_by_rest.setdefault(key[1][:-4], []).append(fnode)

    def _pick_lowest_tier(hits: list[dict]) -> dict | None:
        if len(hits) == 1:
            return hits[0]
        if not hits:
            return None
        ranked = sorted(
            hits, key=lambda f: (_convention_key(f.get("source_file")) or (None, None, 9))[2]
        )
        best = (_convention_key(ranked[0].get("source_file")) or (None, None, 9))[2]
        top = [f for f in ranked
               if (_convention_key(f.get("source_file")) or (None, None, 9))[2] == best]
        return top[0] if len(top) == 1 else None

    for node in list(all_nodes):
        if node.get("source_file"):
            continue
        target = None
        dotted = node.get("cfml_viewlet_event")
        if dotted:
            parts = [p for p in str(dotted).lower().split(".") if p]
            if len(parts) >= 2:
                comp = _pick_lowest_tier(handler_comp_by_rest.get("/".join(parts[:-1]), []))
                if comp is not None:
                    method_nid = method_index.get((comp["id"], parts[-1]))
                    target = node_by_id.get(method_nid) if method_nid else comp
        else:
            vpath = node.get("cfml_view_path")
            if vpath:
                rest = str(vpath).lower().lstrip("/")
                target = _pick_lowest_tier(view_by_rest.get(rest, []))
        if target is not None:
            for edge in all_edges:
                if edge.get("target") == node["id"]:
                    edge["target"] = target["id"]

    # ---- 6e. bare helper UDF calls → the helper function -------------------
    # ColdBox cfincludes every /helpers/*.cfm into handler/view scope, so those
    # UDFs are called bare with no import, no receiver and no registration —
    # invisible to the extractor's same-file call resolution. Bind unresolved
    # bare calls to a uniquely-named helper UDF.
    helper_fn_by_name: dict[str, list[str]] = {}
    for e in all_edges:
        if e.get("relation") not in ("contains", "method"):
            continue
        tgt = node_by_id.get(e.get("target"))
        if tgt is None:
            continue
        src_path = _norm(tgt.get("source_file")).lower()
        if "/helpers/" not in src_path:
            continue
        label = str(tgt.get("label", ""))
        if label.endswith("()"):
            helper_fn_by_name.setdefault(label[:-2].lower(), []).append(tgt["id"])
    if helper_fn_by_name:
        for rc in cfml_raw:
            if rc.get("is_member_call") or rc.get("is_super_call"):
                continue
            caller = rc.get("caller_nid")
            callee = str(rc.get("callee", "")).lower()
            if not caller or caller not in node_by_id:
                continue
            targets = helper_fn_by_name.get(callee, [])
            if len(targets) == 1 and targets[0] != caller:
                _emit(node_by_id[caller], node_by_id[targets[0]], "calls", "helper_udf",
                      source_file=rc.get("source_file", ""),
                      location=rc.get("source_location"))

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
        ("preside-object", "i18n", "webflow-event", "webflow-ref", "webflow-form",
         "interception-point", "renders_viewlet", "renders_view", "sets_view",
         "runs_event")
    ]
    if dead:
        dead_ids = {n["id"] for n in dead}
        all_nodes[:] = [n for n in all_nodes if n.get("id") not in dead_ids]
