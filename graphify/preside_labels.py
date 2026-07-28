"""Deterministic, domain-aware community labels for Preside/ColdBox corpora.

The generic fallbacks name a community after its highest-degree node, which on a
Preside graph produces things like ``getRecords`` or ``index`` — accurate and
useless, because dozens of communities share those hub names. An LLM pass gives
better names but costs tokens per community, and these graphs run to 4,000+.

Preside does not need either. It is a convention-based framework: the folder
path *is* the domain. ``handlers/page-types/exam_booking_page.cfc`` is the exam
booking page type; ``extensions/preside-ext-payments/services/`` is the payments
service layer. So a label can be derived from the paths of a community's members
with no model and no ambiguity — and it stays correct as the code changes.

Output shape, most specific part first::

    exam_booking_page · page type
    payments · services (ext)
    crm · preside objects
    SourcebookRouteHandler · route handler
    memberApplication · webflow

Used automatically for corpora that look like Preside; falls back to the generic
hub labeller for anything it cannot place.
"""
from __future__ import annotations

import collections
import re
from pathlib import PurePosixPath

# Convention directory -> how a developer refers to that kind of thing. Order
# matters: the first marker found in the path wins, so more specific
# sub-conventions must precede their parent (page-types before handlers).
_KIND_RULES: tuple[tuple[str, str], ...] = (
    # Layer markers first: a file under preside/system/services/ is core, not
    # "services", and static/ is the frontend build, not the application.
    ("/preside/system/", "preside core"),
    ("/static/templates/", "frontend template"),
    ("/static/", "frontend"),
    ("/handlers/page-types/", "page type"),
    ("/views/page-types/", "page type view"),
    ("/preside-objects/page-types/", "page type object"),
    ("/handlers/admin/datamanager/", "admin datamanager"),
    ("/handlers/rules/expressions/", "rules expression"),
    ("/handlers/rules/contexts/", "rules context"),
    ("/handlers/formcontrols/", "form control"),
    ("/views/formcontrols/", "form control view"),
    ("/handlers/renderers/", "renderer"),
    ("/handlers/widgets/", "widget"),
    ("/views/widgets/", "widget view"),
    ("/handlers/webflow/", "webflow handler"),
    ("/views/webflow/", "webflow view"),
    ("/workflow/webflows/", "webflow"),
    ("/workflow/", "workflow"),
    ("/handlers/email/", "email handler"),
    ("/views/email/", "email view"),
    ("/handlers/admin/", "admin handler"),
    ("/handlers/dbmigrations/", "db migration"),
    ("/services/routehandlers/", "route handler"),
    ("/services/routehandler/", "route handler"),
    ("/interceptors/", "interceptor"),
    ("/preside-objects/", "preside objects"),
    ("/services/", "services"),
    ("/handlers/", "handlers"),
    ("/views/", "views"),
    ("/layouts/", "layouts"),
    ("/forms/", "forms"),
    ("/i18n/", "i18n"),
    ("/helpers/", "helpers"),
    ("/base/", "base"),
    ("/config/", "config"),
    ("/soap-templates/", "soap templates"),
)

_EXT_RE = re.compile(r"/application/(extensions_app|extensions)/([^/]+)/")
_STOP_STEMS = {"index", "init", "application", "config", "tasks", "general"}


def _norm(p: object) -> str:
    s = str(p or "").replace("\\", "/").lower()
    return s if s.startswith("/") else "/" + s


def _kind_of(path: str) -> str | None:
    """Convention kind for a path. Markers are lowercase; real paths are not
    (`services/routeHandlers/`), so match case-insensitively."""
    low = path.lower()
    for marker, kind in _KIND_RULES:
        if marker in low:
            return kind
    return None


def _module_of(path: str) -> str | None:
    """The owning extension, shortened — `preside-ext-payments` reads `payments`."""
    m = _EXT_RE.search(path)
    if not m:
        return None
    name = m.group(2)
    return name[len("preside-ext-"):] if name.startswith("preside-ext-") else name


def _subject_of(path: str, kind: str | None) -> str | None:
    """The thing itself: the page-type/widget/webflow name, else the file stem."""
    if kind in ("page type", "page type view", "page type object", "widget",
                "widget view", "form control", "form control view",
                "webflow handler", "webflow view", "webflow", "renderer"):
        # The segment after the convention dir names the thing; for views it is
        # a directory (views/widgets/<name>/index.cfm), for handlers a file.
        # Locate on the lowered path, slice from the original to keep casing.
        low = path.lower()
        for marker, k in _KIND_RULES:
            if k == kind and marker in low:
                cut = low.index(marker) + len(marker)
                return PurePosixPath(path[cut:].split("/", 1)[0]).stem or None
    return PurePosixPath(path).stem or None


def label_communities_by_preside_convention(
    G, communities: dict[int, list[str]]
) -> dict[int, str]:
    """``{cid: label}`` for communities whose members sit in Preside conventions.

    A community only gets a label when a clear majority of its *sourced* members
    agree on the convention; otherwise it is omitted so the caller can fall back.
    Requiring agreement is what stops a mixed community being labelled after
    whichever file happened to sort first.
    """
    labels: dict[int, str] = {}
    for cid, members in communities.items():
        # `source_file` is stored lowercased, so the path can be matched but not
        # displayed. Node labels keep the author's casing
        # ("application/handlers/Groups.cfc", "EmsEventRouteHandler"), so build a
        # lowered-stem -> as-written lookup from them and restore case at the end.
        paths = []
        case_of: dict[str, str] = {}
        for n in members:
            if n not in G:
                continue
            data = G.nodes[n]
            sf = data.get("source_file")
            if sf:
                raw = str(sf).replace("\\", "/")
                paths.append(raw if raw.startswith("/") else "/" + raw)
            lbl = str(data.get("label") or "").strip()
            if lbl:
                stem = PurePosixPath(lbl.rstrip("()")).stem
                if stem and stem.lower() != stem:
                    case_of.setdefault(stem.lower(), stem)
        if not paths:
            continue

        kinds = collections.Counter(k for k in (_kind_of(p) for p in paths) if k)
        if not kinds:
            continue
        kind, kind_n = kinds.most_common(1)[0]
        if kind_n * 2 < len(paths):        # no majority — leave to the fallback
            continue

        in_kind = [p for p in paths if _kind_of(p) == kind]
        subjects = collections.Counter(
            s for s in (_subject_of(p, kind) for p in in_kind)
            if s and s not in _STOP_STEMS
        )
        modules = collections.Counter(m for m in (_module_of(p) for p in in_kind) if m)

        parts = []
        if subjects:
            subject, sub_n = subjects.most_common(1)[0]
            # Only lead with the subject when it genuinely characterises the
            # community; a 1-of-20 stem would be actively misleading.
            if sub_n * 3 >= len(in_kind):
                parts.append(case_of.get(subject, subject))
        parts.append(kind)
        label = " · ".join(parts)
        if modules:
            module, mod_n = modules.most_common(1)[0]
            if mod_n * 2 >= len(in_kind):
                label += f" ({module})"
        labels[cid] = label
    return labels


def looks_like_preside(G) -> bool:
    """True when enough of the graph sits under Preside's conventions to bother.

    Scans every node. An earlier version sampled the first 400 for speed and got
    the wrong answer on three of seven real corpora, because node order is not
    random — build scripts and static assets sort first, so the sample missed
    the application tree entirely (servicedesk scored 0/400 on a codebase that
    is overwhelmingly Preside). A string test over 50,000 nodes is milliseconds;
    the sampling bought nothing and cost correctness.

    The threshold is deliberately low: a Preside repo also carries a frontend
    build, docs, CI config and vendored assets, so the application tree does not
    need to be a majority for the conventions to be worth applying.
    """
    seen = hits = 0
    for _n, data in G.nodes(data=True):
        sf = data.get("source_file")
        if not sf:
            continue
        seen += 1
        low = _norm(sf)
        if "/application/" in low or "/preside/system/" in low:
            hits += 1
    return seen > 0 and hits >= max(20, seen * 0.2)
