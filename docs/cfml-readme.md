# CFML / ColdFusion support

graphify indexes CFML (Adobe ColdFusion and Lucee) with a tree-sitter grammar, and
adds a framework-resolution pass for **ColdBox**, **Preside CMS** and
**ReadyMembership** projects.

CFML is not like the other 36 grammars in one important way: in a Preside app most of
the structure is *convention*, not syntax. `extends="app.extensions.preside-ext-x.handlers.y"`
is a dotted mapping path, not a file reference. `property name="foo" inject="FooService";`
is dependency injection resolved by filename at runtime. A file at
`application/handlers/DataFilters.cfc` silently overrides one at
`application/extensions/preside-ext-payments/handlers/DataFilters.cfc`. None of that is
visible to an AST walk alone, so graphify runs a second pass that resolves it.

- **Setup and how it works** — this page
- **Practical recipes** — [cfml-howto.md](cfml-howto.md)

---

## What gets extracted

| Extension | Parsed as |
|---|---|
| `.cfc` | Script components via the `cfscript` grammar; tag components (`<cfcomponent>`) via the `cfml` grammar — chosen by sniffing the first non-whitespace byte |
| `.cfm` | Tag templates via the `cfml` grammar; embedded `<cfscript>` blocks are re-parsed with the `cfscript` grammar so their calls are not lost |
| `.cfml` | Same as `.cfm` |

Both styles yield the same node and edge shapes, so a graph over a mixed codebase is
uniform.

### Nodes

| Node | From |
|---|---|
| File | every `.cfc` / `.cfm` |
| Component | `component { }` and `<cfcomponent>` — carries `cfml_extends` (the raw dotted path) when it extends something |
| Function | `function foo()`, `public any function foo()`, and `<cffunction name="foo">` |
| Query | `<cfquery name="x">` |
| Stub | cross-file targets not yet bound: an extends base, an injected service, `preside-object:<name>` |

Stubs are emitted `type: "module"` so the same service or preside object referenced from
50 files stays **one** shared node instead of fragmenting into 50 file-salted duplicates.

### Edges

| Relation | Context | Meaning |
|---|---|---|
| `contains` / `method` | | file → component, component → function |
| `calls` | `call` | resolved same-file call |
| `calls` | `injected_call` | call through a WireBox-injected service, bound to the target service's method |
| `calls` | `super_call` | `super.method()` bound through the resolved inheritance chain |
| `inherits` | `extends` | `extends="..."`, resolved to the real component where the mapping path can be located |
| `overrides` | `handlers`, `services`, `views`, `preside-objects`, `layouts`, … | project file overriding an extension/core file at the same convention path |
| `uses` | `wirebox_inject` | `property name="x" inject="Y"` DI declaration |
| `uses` | `preside_object` | `getPresideObject("ems_event")` — the data-layer dependency map |
| `references` | `renders` | handler action → its convention view (`handlers/a/b.cfc::act` → `views/a/b/act.cfm`) |
| `references` | `cfinclude` | `<cfinclude template="...">` |

`overrides` is the one to reach for when you are about to write Preside code: it is the
project → extension → core resolution order, materialised.

**ColdBox injection DSLs are kept, not discarded.** `inject="coldbox:setting:payments.orderNumberPrefix"`
yields a `setting:payments.orderNumberPrefix` node rather than being dropped as an
unresolvable service. That is deliberate: it gives you a configuration-dependency map
alongside the service graph — every component that reads a given setting is one hop from
it. Only the last DSL segment is used as the target name, so `delayedInjector:FooService`
and `FooService` converge on the same node.

### Noise filtering

CFML is case-insensitive, so built-in filtering compares lowercased. Two sets are
excluded as call targets:

- **~350 CFML/Lucee built-ins** (`arrayLen`, `structKeyExists`, `queryExecute`, `isValid`, …)
- **Preside/ColdBox superclass proxies and helper UDFs** (`renderView`, `$getPresideSetting`,
  `translateResource`, `isTrue`, `queryRowToStruct`, …), matched with or without the `$` prefix

Without this every Preside file calls the same 40 framework functions and they become the
top god-nodes of the graph, drowning the real call structure. `getPresideObject()` is
special-cased *before* the filter so its string argument is captured as a data-layer edge
rather than discarded.

---

## Setup

### 1. The grammar

CFML support uses [`tree-sitter-cfml`](https://github.com/cfmleditor/tree-sitter-cfml)
(the cfmleditor grammar, which ships `cfscript`, `cfml`, `cfhtml` and `cfquery`). It is a
declared dependency, so a normal install brings it in:

```bash
uv tool install graphifyy
```

Verify it resolved:

```bash
graphify --version
python -c "import tree_sitter_cfml; print('ok')"
```

If you are working from a source checkout, install it **editable** so the checkout is
the live source — edits take effect immediately, with no reinstall between changes:

```bash
cd /path/to/graphify
uv tool install --force --editable --from . graphifyy
```

Confirm the binary resolves to your checkout rather than a built copy:

```bash
"$(dirname "$(readlink -f "$(command -v graphify)")")/python" \
  -c "import graphify, os; print(os.path.dirname(graphify.__file__))"
```

Never run `uv tool upgrade graphifyy` against such an install — it replaces the editable
link with the PyPI build and your local grammar work disappears. Re-run the install
command above after pulling upstream changes.

If the grammar is missing, CFML files still classify as code but the extractor returns
`tree-sitter-cfml not installed` and produces no nodes for them.

### 2. Tell graphify what to scan

This is the step that matters most on a Preside project, and the default is wrong for
you in two directions.

**Vendored trees must be excluded.** A checked-out Preside project contains the Preside
core (`website/preside/`), generated static assets, uploads, logs, and often a live
MySQL data directory. Indexing them is slow and produces nothing useful.

**Installed extensions must be *included*.** `application/extensions/` is gitignored in
every Pixl8 project — the extensions are installed by CommandBox, not committed. But
they are exactly the read-only context you need: the classes your project overrides and
the services it injects live there. graphify honours `.gitignore` by default, so you
must opt out of that with `--no-gitignore` and control scope with `.graphifyignore`
instead.

Drop this at the project root as `.graphifyignore`:

```gitignore
# Preside core + generated static
website/preside/
static/_core/
website/assets/
website/uploads/
website/logs/

# Build/dependency dirs
node_modules/
.local/
logs/
tmp/

# Vendored/minified frontend
*.min.js
*.min.css
*.map
website/application/extensions/*/assets/
website/application/extensions/*/static/

# Framework vendor modules inside extensions (not your code)
website/application/extensions/*/modules/

# Data/secrets-shaped
*.sql.gz
*.dump
.env*

graphify-out/
```

### 3. Build

```bash
graphify extract . --code-only --no-gitignore
```

`--code-only` skips docs/images and makes the build **free and fully local** — no API
key, no tokens. Add community detection afterwards:

```bash
graphify cluster-only . --no-viz --no-label
```

Then keep it current:

```bash
graphify hook install     # post-commit + post-checkout rebuild
graphify claude install   # CLAUDE.md section + query-before-grep hooks
```

Recipes for querying, reviewing changes, and rolling this out across many projects are
in [cfml-howto.md](cfml-howto.md).

---

## Reference numbers

A real Preside/ReadyMembership project (a Pixl8 internal system: 4,190 scanned files —
project code plus ~40 installed extensions):

| | |
|---|---|
| Build time | ~13s (code-only, 12 workers, no API calls) |
| Graph | 23,376 nodes · 36,338 edges · 2,825 communities |
| Components / functions | 2,119 components with methods · 13,600 method edges |
| Calls | 7,858 same-file · 3,834 injected-service · 73 `super` — **0% dangling** |
| Framework relations | 647 `inherits` · 2,796 `wirebox_inject` · 512 handler→view · 64 `overrides` |
| Data layer | 1,862 edges onto 267 distinct preside objects |
| Token benchmark | **125.7× fewer tokens per query** vs feeding the corpus (`graphify benchmark`) |

---

## Known limits

- **Tag-mode `.cfm` views are shallow by design.** A view with no `<cffunction>` and no
  `<cfscript>` yields a file node and its `cfinclude`/render relationships, not much
  more. That is usually correct — the logic lives in the handler.
- **`extends` resolves only when the mapping path can be located on disk.** A dotted path
  pointing at an extension that is not installed leaves the edge on a stub. In the
  reference project 481 of 647 resolved; the remainder are genuinely absent code.
- **Injection resolution requires an unambiguous filename.** Two same-named services in
  different extensions with no project-level winner produce no edge rather than a guess.
- **Not yet modelled:** form XML (`forms/**/*.xml`), i18n `.properties`, webflow `.yml`
  step→handler wiring, and `Config.cfc` interceptor registrations. These are real Preside
  semantics that a future pass could add.
- `.cfhtml` and the standalone `cfquery` grammar ship with tree-sitter-cfml but are not
  wired up.

---

## Adding to the framework pass

The ColdBox/Preside logic lives in `graphify/preside_resolution.py`, registered as a
`LanguageResolver` in `extract.py` — the same seam the Ruby and Pascal resolvers use. It
runs after per-file extraction and after node-id disambiguation, so it sees final ids.
Each pass follows a god-node guard: emit an edge only when there is exactly one
unambiguous candidate. The extractor itself is `graphify/extractors/cfml.py`. See
[ARCHITECTURE.md](../ARCHITECTURE.md) for the general "how to add a language" path.
