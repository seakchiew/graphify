"""Domain-aware community labels for Preside corpora.

Generic labellers name a community after its highest-degree node, which on a
Preside graph yields `index`, `getRecords`, `init` — accurate and useless,
because hundreds of communities share those hubs. An LLM pass names them well
but costs tokens per community, and these graphs run to 4,000+.

Preside is convention-based, so the folder path *is* the domain and the label
can be derived deterministically for free.
"""
from __future__ import annotations

import networkx as nx
import pytest

from graphify.preside_labels import (
    _kind_of,
    _module_of,
    _subject_of,
    label_communities_by_preside_convention,
    looks_like_preside,
)

APP = "website/application"


def _graph(nodes):
    """nodes: list of (id, label, source_file)."""
    G = nx.Graph()
    for nid, label, src in nodes:
        G.add_node(nid, label=label, source_file=src)
    return G


@pytest.mark.parametrize("path,kind", [
    (f"/{APP}/handlers/page-types/exam_booking_page.cfc", "page type"),
    (f"/{APP}/handlers/admin/datamanager/crm_contact.cfc", "admin datamanager"),
    (f"/{APP}/services/routeHandlers/EmsEventRouteHandler.cfc", "route handler"),
    (f"/{APP}/interceptors/PriiInterceptor.cfc", "interceptor"),
    (f"/{APP}/workflow/webflows/memberApplication.yml", "webflow"),
    (f"/{APP}/preside-objects/crm/crm_contact.cfc", "preside objects"),
    (f"/{APP}/services/PaymentOrderService.cfc", "services"),
    ("/static/templates/pages/home.cfm", "frontend template"),
])
def test_kind_from_convention(path, kind):
    assert _kind_of(path) == kind


def test_more_specific_convention_wins():
    """page-types must beat the bare /handlers/ rule it sits inside."""
    assert _kind_of(f"/{APP}/handlers/page-types/x.cfc") == "page type"
    assert _kind_of(f"/{APP}/views/widgets/banner/index.cfm") == "widget view"


def test_module_is_shortened():
    p = f"/{APP}/extensions/preside-ext-payments/services/S.cfc"
    assert _module_of(p) == "payments"
    assert _module_of(f"/{APP}/extensions_app/inteleos-cpd/services/S.cfc") == "inteleos-cpd"
    assert _module_of(f"/{APP}/services/S.cfc") is None


def test_subject_names_the_thing_not_the_file():
    """For a widget the subject is the widget, not `index`."""
    p = f"/{APP}/views/widgets/bannerCarousel/index.cfm"
    assert _subject_of(p, _kind_of(p)) == "bannerCarousel"


def test_community_labelled_by_convention():
    G = _graph([
        ("a", "PaymentOrderService", f"{APP}/extensions/preside-ext-payments/services/PaymentOrderService.cfc"),
        ("b", "createOrder()", f"{APP}/extensions/preside-ext-payments/services/PaymentOrderService.cfc"),
        ("c", "refund()", f"{APP}/extensions/preside-ext-payments/services/PaymentOrderService.cfc"),
    ])
    labels = label_communities_by_preside_convention(G, {1: ["a", "b", "c"]})
    assert labels[1] == "PaymentOrderService · services (payments)"


def test_mixed_community_is_left_to_the_fallback():
    """No majority convention means no label — better a hub name than a wrong one."""
    G = _graph([
        ("a", "A", f"{APP}/services/A.cfc"),
        ("b", "B", f"{APP}/handlers/B.cfc"),
        ("c", "C", "static/templates/pages/c.cfm"),
        ("d", "D", "website/preside/system/services/D.cfc"),
    ])
    assert label_communities_by_preside_convention(G, {1: ["a", "b", "c", "d"]}) == {}


def test_generic_stems_do_not_become_the_subject():
    """Half a Preside codebase is called `index`; leading with it says nothing."""
    G = _graph([
        ("a", "index", f"{APP}/views/foo/index.cfm"),
        ("b", "index", f"{APP}/views/bar/index.cfm"),
        ("c", "index", f"{APP}/views/baz/index.cfm"),
    ])
    assert label_communities_by_preside_convention(G, {1: ["a", "b", "c"]})[1] == "views"


def test_module_suffix_needs_a_majority():
    """One stray file from another extension must not rename the community."""
    G = _graph([
        ("a", "S1", f"{APP}/extensions/preside-ext-ems/services/S1.cfc"),
        ("b", "S2", f"{APP}/extensions/preside-ext-ems/services/S2.cfc"),
        ("c", "S3", f"{APP}/extensions/preside-ext-ems/services/S3.cfc"),
        ("d", "X", f"{APP}/extensions/preside-ext-payments/services/X.cfc"),
    ])
    assert label_communities_by_preside_convention(G, {1: list("abcd")})[1].endswith("(ems)")


def test_looks_like_preside_gates_non_preside_corpora():
    assert looks_like_preside(_graph(
        [(f"a{i}", "A", f"{APP}/services/A{i}.cfc") for i in range(25)]
    ))
    assert not looks_like_preside(_graph([
        ("a", "A", "src/main.py"), ("b", "B", "src/util.py"),
    ]))


def test_looks_like_preside_survives_unlucky_node_ordering():
    """A real Preside repo also carries build scripts, CI config and a frontend
    build, and those can sort first. An earlier sampled implementation read the
    first 400 nodes and scored one such corpus 0/400 — the detector must look at
    the whole graph, and must not need the application tree to be a majority."""
    nodes = [(f"b{i}", "build", f"buildAssets/script{i}.sh") for i in range(300)]
    nodes += [(f"a{i}", "Svc", f"{APP}/services/Svc{i}.cfc") for i in range(120)]
    assert looks_like_preside(_graph(nodes))
