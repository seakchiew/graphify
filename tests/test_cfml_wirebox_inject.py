"""WireBox injection edges in CFML/Preside corpora.

Two defects motivated these tests, both found by grepping six production Preside
codebases and comparing the counts against the graph:

1. Preside services declare dependencies two ways — `property ... inject="X"`
   and a `@arg.inject X` docblock above `init()`. Only the property form was
   parsed, losing 5-32% of the dependency graph per corpus (221 of 683 edges on
   the worst one).

2. Not every `inject=` value is a component id. `coldbox:setting:foo.bar` split
   naively on the first colon produced a component named `setting:foo.bar`;
   one corpus carried 116 such phantom nodes, competing with real components for
   the same query terms. `presidecms:object:x` is a data-layer dependency and
   belongs on the same hub as `getPresideObject("x")`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from graphify.extract import extract
from graphify.extractors.cfml import _wirebox_target


# ---------------------------------------------------------------- unit: DSL

@pytest.mark.parametrize("dsl,expected", [
    # plain component ids
    ("FormsService", ("component", "FormsService")),
    ("  SpacedService  ", ("component", "SpacedService")),
    # lazy wrappers unwrap to the component they proxy
    ("delayedInjector:MyService", ("component", "MyService")),
    ("provider:MyService", ("component", "MyService")),
    ("DELAYEDINJECTOR:MyService", ("component", "MyService")),
    ("provider:delayedInjector:MyService", ("component", "MyService")),
    ("model:MyService", ("component", "MyService")),
    # preside objects are a data-layer edge, not a component dependency
    ("presidecms:object:website_user", ("preside_object", "website_user")),
    ("presidecms:object:CRM_Contact", ("preside_object", "crm_contact")),
    # settings, loggers, caches and framework singletons are not graph nodes
    ("coldbox:setting:groups.enabled", ("", "")),
    ("coldbox:plugin:messageBox", ("", "")),
    ("coldbox:interceptorService", ("", "")),
    ("logbox:logger:pearsonvue", ("", "")),
    ("cachebox:viewletCache", ("", "")),
    ("presidecms:directories:soap-templates", ("", "")),
    ("java:java.lang.System", ("", "")),
    # nothing usable
    ("", ("", "")),
    ("   ", ("", "")),
])
def test_wirebox_target(dsl, expected):
    assert _wirebox_target(dsl) == expected


def test_wirebox_target_rejects_unknown_namespace():
    """An unrecognised `ns:value` is not silently turned into a component.

    Guessing here is what minted the phantom nodes; an unknown namespace is
    more likely a new DSL than a component whose name contains a colon.
    """
    kind, _ = _wirebox_target("somefuturens:Thing")
    assert kind == ""


# --------------------------------------------------------- integration: edges

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


def _inject_targets(result, context="wirebox_inject"):
    by_id = {n["id"]: n for n in result["nodes"]}
    return sorted(
        str(by_id.get(e["target"], {}).get("label", ""))
        for e in result["edges"] if e.get("context") == context
    )


SERVICE = """/**
 * @presideService true
 */
component {

\tproperty name="violationService" inject="ViolationService";
\tproperty name="blDocTypes"       inject="coldbox:setting:bluelight.blDocTypes";
\tproperty name="contactDao"       inject="presidecms:object:crm_contact";

\t/**
\t * @formsService.inject         FormsService
\t * @webflowConfigurator.inject  webflowConfigurationService
\t * @auditLogger.inject          logbox:logger:audit
\t */
\tpublic any function init(
\t\t  required any formsService
\t\t, required any webflowConfigurator
\t\t, required any auditLogger
\t) {
\t\treturn this;
\t}
}
"""


def test_docblock_and_property_injection_both_emit_edges(tmp_path):
    r = _extract(tmp_path, {"ExamService.cfc": SERVICE})
    targets = _inject_targets(r)
    # property form (pre-existing behaviour) plus docblock form (the fix)
    assert "ViolationService" in targets
    assert "FormsService" in targets
    assert "webflowConfigurationService" in targets


def test_non_component_dsl_creates_no_phantom_node(tmp_path):
    r = _extract(tmp_path, {"ExamService.cfc": SERVICE})
    targets = _inject_targets(r)
    assert not [t for t in targets if ":" in t], f"phantom stubs: {targets}"
    # specifically: a setting and a logger are injected above and must not appear
    assert not [t for t in targets if "setting" in t.lower() or "audit" in t.lower()]


def test_presidecms_object_injection_lands_on_the_data_layer_hub(tmp_path):
    r = _extract(tmp_path, {"ExamService.cfc": SERVICE})
    assert "preside-object:crm_contact" in _inject_targets(r, context="preside_object")


def test_docblock_injection_is_not_confused_by_other_annotations(tmp_path):
    """`@presideService`/`@singleton` and an `@x.inject` must not cross-talk."""
    src = """/**
 * @presideService true
 * @singleton      true
 */
component {
\t/**
\t * @only.inject TheOnlyService
\t */
\tpublic any function init( required any only ) { return this; }
}
"""
    r = _extract(tmp_path, {"Svc.cfc": src})
    assert _inject_targets(r) == ["TheOnlyService"]
