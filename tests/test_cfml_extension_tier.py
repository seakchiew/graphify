"""Which modules under application/extensions/ are project-authored.

`extensions_app/` is the modern home for project modules, but older projects put
them in `extensions/` next to the installed ones. Tagging by directory alone
marks them OOB, and layer-weighted retrieval then *under*-ranks the code the
project actually owns — on two of seven production corpora that was 194 and
~2,400 nodes of first-party code ranked as vendor code.

What separates them is git: installed extensions are gitignored and restored by
`box install`, project-authored ones are committed. A `preside-ext-` naming rule
gets it wrong in both directions — `common-editable-form-content` is unprefixed
but box-installed, and nothing stops a project committing a module under any
name — so git is the only signal used.

When git cannot answer, everything stays `extension`: the pre-existing
behaviour. The signal only ever promotes; it never guesses.
"""
from __future__ import annotations

import subprocess

import pytest

from graphify.preside_resolution import (
    _extension_module_layer,
    _layer_of,
    _tracked_extension_modules,
)


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _repo(tmp_path, modules: dict[str, bool]):
    """Build a repo; modules maps name -> committed?"""
    ext = tmp_path / "website" / "application" / "extensions"
    for name, committed in modules.items():
        d = ext / name / "services"
        d.mkdir(parents=True)
        (d / "Svc.cfc").write_text("component { public any function init(){ return this; } }")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    gitignore = "\n".join(f"website/application/extensions/{n}/"
                          for n, c in modules.items() if not c)
    (tmp_path / ".gitignore").write_text(gitignore + "\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    _tracked_extension_modules.cache_clear()
    return tmp_path


def test_committed_module_is_project_authored(tmp_path):
    root = _repo(tmp_path, {"preside-ext-payments": False, "acme-custom": True})
    base = "/website/application/extensions/"
    assert _extension_module_layer(base + "acme-custom/services/svc.cfc", str(root)) == "app-extension"
    assert _extension_module_layer(base + "preside-ext-payments/services/svc.cfc", str(root)) == "extension"


def test_unprefixed_but_installed_module_stays_oob(tmp_path):
    """Real case: `common-editable-form-content` is a shared Pixl8 extension
    with no `preside-ext-` prefix, box-installed on two corpora. A naming rule
    would promote it to project code; git correctly does not."""
    root = _repo(tmp_path, {"common-shared-thing": False, "acme-custom": True})
    base = "/website/application/extensions/"
    assert _extension_module_layer(base + "common-shared-thing/services/svc.cfc", str(root)) == "extension"
    assert _extension_module_layer(base + "acme-custom/services/svc.cfc", str(root)) == "app-extension"


def test_conventionally_named_project_module_is_still_promoted(tmp_path):
    """Nothing stops a project committing a module called `preside-ext-*`.
    Git says project; there is no naming rule left to disagree."""
    root = _repo(tmp_path, {"preside-ext-vendor": False, "preside-ext-ours": True})
    base = "/website/application/extensions/"
    assert _extension_module_layer(base + "preside-ext-ours/x.cfc", str(root)) == "app-extension"
    assert _extension_module_layer(base + "preside-ext-vendor/x.cfc", str(root)) == "extension"


def test_no_git_leaves_everything_as_extension(tmp_path):
    """Degrades to the pre-existing behaviour rather than guessing."""
    ext = tmp_path / "website" / "application" / "extensions"
    for name in ("preside-ext-payments", "acme-custom"):
        (ext / name).mkdir(parents=True)
    _tracked_extension_modules.cache_clear()
    base = "/website/application/extensions/"
    assert _tracked_extension_modules(str(tmp_path)) is None
    assert _extension_module_layer(base + "acme-custom/x.cfc", str(tmp_path)) == "extension"
    assert _extension_module_layer(base + "preside-ext-payments/x.cfc", str(tmp_path)) == "extension"


def test_all_vendored_makes_git_uninformative(tmp_path):
    """A project that commits every extension must not have all 70 OOB modules
    classified as project code — "tracked" only discriminates because installed
    extensions are normally gitignored."""
    root = _repo(tmp_path, {"preside-ext-payments": True, "acme-custom": True})
    assert _tracked_extension_modules(str(root)) is None
    base = "/website/application/extensions/"
    assert _extension_module_layer(base + "preside-ext-payments/x.cfc", str(root)) == "extension"
    assert _extension_module_layer(base + "acme-custom/x.cfc", str(root)) == "extension"


@pytest.mark.parametrize("path,expected", [
    ("website/application/extensions_app/acme-thing/services/S.cfc", "app-extension"),
    ("website/application/handlers/Foo.cfc", "app"),
    ("website/preside/system/services/Bar.cfc", "core"),
    ("static/templates/pages/home.cfm", "static"),
])
def test_other_layers_unaffected(path, expected):
    assert _layer_of(path) == expected
