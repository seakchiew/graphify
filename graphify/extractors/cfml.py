"""CFML/CFScript extractor (ColdFusion — Lucee/Adobe CF, ColdBox/Preside apps).

Uses tree-sitter-cfml (cfmleditor grammar), which ships three languages:
``cfscript`` for script-style ``.cfc`` components, and ``cfml`` for tag-style
templates (``.cfm`` views/layouts and legacy ``<cfcomponent>`` CFCs). A ``.cfc``
is sniffed: tag-style files (leading ``<``) route to the tag extractor, script
files to the cfscript extractor; ``<cfscript>`` blocks inside tag files are
re-parsed with the cfscript grammar so their calls are not lost.

Emits per file:
- a component node (script ``component {}`` or tag ``<cfcomponent>``) with
  ``method`` edges to its functions — matching the type/method shape the
  corpus-level member-call resolver expects;
- ``inherits`` edges for ``extends="..."`` (sourceless stub target; the raw
  dotted path is kept on the component node as ``cfml_extends`` so a
  framework-aware post-pass can resolve ColdBox/Preside mapping paths);
- ``uses`` edges (context ``wirebox_inject``) for ``property ... inject="X"``
  DI declarations, plus a per-component receiver map so member calls through
  injected services surface as raw_calls with an ``injected_target`` hint;
- ``uses`` edges (context ``preside_object``) to ``preside-object:<name>``
  stubs for ``getPresideObject("x")`` / ``$getPresideObject("x")`` calls —
  the data-layer dependency map of a Preside app;
- ``references`` edges for ``<cfinclude template="...">``;
- ``calls`` edges resolved same-file, everything else deferred to raw_calls.

CFML built-ins are case-insensitive; the filter below compares lowercased so
``ArrayLen``/``arraylen`` never become god nodes (#726). Preside/ColdBox
superclass proxies (``renderView``, ``$getPresideSetting``…) are filtered the
same way — they are framework built-ins in effect and would otherwise be the
top god nodes of every Preside codebase.
"""
from __future__ import annotations

import re
from pathlib import Path

from graphify.extractors.base import _file_stem, _make_id, _read_text


# Common CFML/Lucee built-in functions (lowercased — CFML is case-insensitive).
_CFML_BUILTINS: frozenset[str] = frozenset({
    # arrays
    "arraylen", "arrayappend", "arrayprepend", "arraydeleteat", "arrayinsertat",
    "arrayfind", "arrayfindnocase", "arrayfindall", "arraycontains", "arraysort",
    "arrayfilter", "arraymap", "arrayreduce", "arrayeach", "arraytolist",
    "arrayclear", "arrayavg", "arraysum", "arraymin", "arraymax", "arraynew",
    "arrayisdefined", "arrayisempty", "arrayslice", "arrayreverse", "arrayfirst",
    "arraylast", "arrayunique", "arrayresize", "arrayset", "arrayswap",
    # structs
    "structkeyexists", "structkeyarray", "structkeylist", "structappend",
    "structcopy", "structnew", "structdelete", "structfind", "structfindkey",
    "structfindvalue", "structcount", "structisempty", "structinsert",
    "structupdate", "structclear", "structget", "structeach", "structfilter",
    "structmap", "structreduce", "structsort", "structtosorted",
    # lists
    "listlen", "listappend", "listprepend", "listfirst", "listlast", "listrest",
    "listgetat", "listsetat", "listdeleteat", "listinsertat", "listfind",
    "listfindnocase", "listcontains", "listcontainsnocase", "listtoarray",
    "listsort", "listfilter", "listmap", "listeach", "listreduce", "listqualify",
    "listchangedelims", "listremoveduplicates", "listvaluecount",
    # strings
    "len", "trim", "ltrim", "rtrim", "lcase", "ucase", "ucfirst", "lcfirst",
    "left", "right", "mid", "find", "findnocase", "findoneof", "reverse",
    "replace", "replacenocase", "replacelist", "repeatstring", "insert",
    "removechars", "spanexcluding", "spanincluding", "compare", "comparenocase",
    "refind", "refindnocase", "rereplace", "rereplacenocase", "rematch",
    "rematchnocase", "wrap", "paragraphformat", "stripcr", "cjustify",
    "ljustify", "rjustify", "formatbasen", "asc", "chr", "canonicalize",
    # type checks / conversion
    "isdefined", "isnull", "isnumeric", "isarray", "isstruct", "isquery",
    "isdate", "isboolean", "isjson", "issimplevalue", "isbinary", "isobject",
    "iscustomfunction", "isclosure", "isvalid", "isinstanceof", "isempty",
    "isxml", "isxmldoc", "isxmlelem", "isxmlnode", "isspreadsheetobject",
    "isimage", "isfileobject", "isipv6", "isleapyear", "islocalhost",
    "val", "tostring", "tobase64", "tobinary", "tonumeric", "javacast",
    "deserializejson", "serializejson", "deserializexml", "serializexml",
    "parsenumber", "lsparsenumber", "lsparsecurrency", "lsparsedatetime",
    # numbers / math
    "abs", "max", "min", "sgn", "sqr", "int", "fix", "round", "ceiling",
    "floor", "rand", "randrange", "randomize", "exp", "log", "log10", "pi",
    "sin", "cos", "tan", "asin", "acos", "atn", "bitand", "bitor", "bitxor",
    "bitnot", "bitshln", "bitshrn", "bitmaskclear", "bitmaskread", "bitmaskset",
    "inputbasen", "precisionevaluate", "increment", "decrement",
    # dates
    "now", "nowserver", "createdate", "createdatetime", "createtime",
    "createtimespan", "createodbcdate", "createodbcdatetime", "createodbctime",
    "dateadd", "datediff", "datepart", "datecompare", "dateconvert",
    "dateformat", "timeformat", "datetimeformat", "lsdateformat",
    "lstimeformat", "lsdatetimeformat", "parsedatetime", "day", "month",
    "year", "hour", "minute", "second", "millisecond", "quarter", "week",
    "dayofweek", "dayofyear", "daysinmonth", "daysinyear", "firstdayofmonth",
    "monthasstring", "dayofweekasstring", "gettimezoneinfo", "gettickcount",
    # formatting
    "numberformat", "decimalformat", "dollarformat", "lsnumberformat",
    "lscurrencyformat", "lseurocurrencyformat", "booleanformat", "yesnoformat",
    "htmleditformat", "htmlcodeformat", "xmlformat", "urlencodedformat",
    "urldecode", "encodeforhtml", "encodeforhtmlattribute", "encodeforjavascript",
    "encodeforurl", "encodeforcss", "encodeforxml", "encodeforxmlattribute",
    "encodefordn", "encodeforldap", "esapiencode", "sanitizehtml",
    # query
    "querynew", "queryaddrow", "queryaddcolumn", "querysetcell", "querygetrow",
    "queryexecute", "queryfilter", "querymap", "queryeach", "queryreduce",
    "querysort", "querycolumnarray", "querycolumnexists", "querycolumnlist",
    "querycolumncount", "queryrecordcount", "querycurrentrow", "querydeleterow",
    "queryrowdata", "valuearray", "valuelist", "quotedvaluelist", "queryconvertforgrid",
    # files / paths
    "fileexists", "directoryexists", "fileread", "filereadbinary", "filewrite",
    "fileappend", "filedelete", "filecopy", "filemove", "fileupload",
    "fileuploadall", "fileopen", "fileclose", "filereadline", "fileseek",
    "filesetaccessmode", "filesetattribute", "filesetlastmodified", "fileinfo",
    "getfileinfo", "directorylist", "directorycreate", "directorydelete",
    "directorycopy", "directoryrename", "expandpath", "contractpath",
    "getdirectoryfrompath", "getfilefrompath", "gettempdirectory",
    "gettempfile", "getcanonicalpath", "getcurrenttemplatepath",
    "getbasetemplatepath", "getbasetagdata", "getbasetaglist",
    # crypto / encoding / ids
    "hash", "hmac", "encrypt", "decrypt", "encryptbinary", "decryptbinary",
    "generatesecretkey", "createuuid", "createguid", "binaryencode",
    "binarydecode", "charsetencode", "charsetdecode", "generatepbkdfkey",
    # system / runtime
    "createobject", "createdynamicproxy", "duplicate", "evaluate", "iif", "de",
    "invoke", "structnew", "getmetadata", "getcomponentmetadata",
    "getfunctioncalledname", "getfunctionlist", "getapplicationsettings",
    "getapplicationmetadata", "getpagecontext", "gethttprequestdata",
    "gethttptimestring", "getclientvariableslist", "getlocale",
    "getlocaledisplayname", "setlocale", "gettimezone", "settimezone",
    "cfusion_decrypt", "cfusion_encrypt", "urlsessionformat", "preservesinglequotes",
    "writeoutput", "writedump", "writelog", "dump", "echo", "abort", "throw",
    "rethrow", "trace", "sleep", "pagepoolclear", "objectequals",
    "sessioninvalidate", "sessionrotate", "csrfgeneratetoken", "csrfverifytoken",
    "applicationstop", "runasync", "transactioncommit", "transactionrollback",
    "transactionsetsavepoint", "location", "getsafehtml", "issafehtml",
    # xml
    "xmlparse", "xmlnew", "xmlsearch", "xmltransform", "xmlchildpos",
    "xmlelemnew", "xmlgetnodetype", "xmlvalidate",
    # images / spreadsheets (common in imports/exports)
    "imagenew", "imageread", "imagewrite", "imageresize", "imagescaletofit",
    "spreadsheetnew", "spreadsheetread", "spreadsheetwrite", "spreadsheetaddrow",
    "spreadsheetaddrows", "spreadsheetformatcell", "spreadsheetsetcellvalue",
})

# Preside/ColdBox superclass proxies and helper UDFs, called bare in
# handlers/views and ``$``-prefixed (or via ``$helpers.``) in services.
# Compared after stripping a leading ``$`` and lowercasing. Filtered like
# built-ins — every Preside file calls these, so as call targets they carry
# no discriminating signal (getPresideObject is special-cased above the
# filter to capture its object-name argument instead).
_PRESIDE_PROXIES: frozenset[str] = frozenset({
    "getpresideobject", "getpresideobjectservice", "getpresidesetting",
    "getsetting", "getsystemsetting", "getcoldboxsetting", "getmodel",
    "getsingleton", "getinstance", "getcontroller", "getcoldbox",
    "getrequestcontext", "getrequestservice", "getplugin", "getinterceptor",
    "renderview", "renderviewlet", "renderlabel", "renderfield",
    "rendercontent", "renderasset", "renderlink", "renderform",
    "renderformcontrol", "renderwebflow", "translateresource",
    "translatevalidationmessages", "hascmspermission", "haswebsitepermission",
    "checkpermission", "isfeatureenabled", "announceinterception",
    "audit", "auditaction", "isloggedin", "iswebsiteuserloggedin",
    "isadminuserloggedin", "getloggedinuserid", "getloggedinuserdetails",
    "getadminloggedinuserid", "getadminloggedinuserdetails", "sendemail",
    "createtask", "createnotification", "runevent", "runroute", "relocate",
    "setnextevent", "buildlink", "buildadminlink", "weburl", "adminurl",
    "event", "addmessage", "logmessage", "raiseerror", "slugify",
    # helper UDFs (bare in handlers/views; $helpers.x in services)
    "istrue", "isfalse", "isemptystring", "firstnonemptystring", "abbreviate",
    "queryrowtostruct", "querytoarray", "getshortdate", "getlongdate",
    "striptags", "hastags", "removeemptystructkeys", "mergestructs",
    "extractdatafromstruct", "obfuscateemail", "formatmoney",
})

# Tag names whose bodies/attributes we mine in tag-mode files.
_TAG_NAME_RE = re.compile(rb"^<cf(\w+)", re.IGNORECASE)
_END_FUNCTION_RE = re.compile(rb"</cffunction\s*>", re.IGNORECASE)


def _attr_map(tag_node, source: bytes) -> dict[str, str]:
    """Collect ``cf_attribute`` name/value pairs from a cf tag node.

    ``<cffunction>`` carries ``cf_attribute`` children directly;
    ``<cfcomponent>`` wraps each in a ``cf_tag_attributes`` node — descend one
    level so both shapes yield the same map."""
    attrs: dict[str, str] = {}

    def collect(node) -> None:
        for child in node.children:
            if child.type == "cf_tag_attributes":
                collect(child)
                continue
            if child.type != "cf_attribute":
                continue
            name = ""
            value = ""
            for sub in child.children:
                if sub.type == "cf_attribute_name":
                    name = _read_text(sub, source).strip().lower()
                elif sub.type in ("quoted_cf_attribute_value", "cf_attribute_value"):
                    value = _read_text(sub, source).strip().strip("\"'")
            if name:
                attrs[name] = value

    collect(tag_node)
    return attrs


def _component_attr_map(component_node, source: bytes) -> dict[str, str]:
    """Collect ``component_attribute`` pairs from a cfscript component/property."""
    attrs: dict[str, str] = {}
    for child in component_node.children:
        if child.type != "component_attribute":
            continue
        name = ""
        value = ""
        for sub in child.children:
            if sub.type == "identifier" and not name:
                name = _read_text(sub, source).strip().lower()
            elif sub.type == "string":
                value = _read_text(sub, source).strip().strip("\"'")
            elif sub.type in ("true", "false"):
                value = sub.type
        if name:
            attrs[name] = value
    return attrs


def _is_filtered_callee(name: str) -> bool:
    bare = name.lstrip("$").lower()
    return bare in _CFML_BUILTINS or bare in _PRESIDE_PROXIES


def _string_literal_arg(call_node, source: bytes) -> str | None:
    """First argument of a call when it is a plain string literal."""
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type == "string":
            return _read_text(child, source).strip().strip("\"'")
        if child.is_named:
            return None
    return None


def extract_cfml(path: Path) -> dict:
    """Extract components, functions, extends/inject/include relationships and
    calls from a CFML file (script or tag style)."""
    try:
        import tree_sitter_cfml as tscfml
        from tree_sitter import Language, Parser
    except ImportError:
        return {"nodes": [], "edges": [], "error": "tree-sitter-cfml not installed"}

    try:
        source = path.read_bytes()
    except OSError as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    stem = _file_stem(path)
    str_path = str(path)
    nodes: list[dict] = []
    edges: list[dict] = []
    raw_calls: list[dict] = []
    seen_ids: set[str] = set()
    seen_call_pairs: set[tuple[str, str]] = set()
    # property-name -> injected service name, for member-call hints
    inject_map: dict[str, str] = {}

    def add_node(nid: str, label: str, line: int, **extra) -> None:
        if nid not in seen_ids:
            seen_ids.add(nid)
            node = {
                "id": nid,
                "label": label,
                "file_type": "code",
                "source_file": str_path,
                "source_location": f"L{line}",
            }
            node.update(extra)
            nodes.append(node)

    def add_stub(name: str, **extra) -> str:
        """Sourceless stub for a cross-file target (extends base, injected
        service, preside object) — the corpus-level rewire collapses it onto
        the real definition when one exists (#1402 pattern). ``type=module``
        exempts the stub from id-disambiguation (#1327): the same service or
        preside object referenced from N files is one shared entity, and
        salting it apart per referencing file would fragment the DI /
        data-layer map into per-file duplicates."""
        nid = _make_id(name)
        if nid not in seen_ids:
            seen_ids.add(nid)
            node = {
                "id": nid,
                "label": name,
                "file_type": "code",
                "type": "module",
                "source_file": "",
                "source_location": "",
                "origin_file": str_path,
            }
            node.update(extra)
            nodes.append(node)
        return nid

    def add_edge(src: str, tgt: str, relation: str, line: int,
                 confidence: str = "EXTRACTED", context: str | None = None) -> None:
        edge = {
            "source": src,
            "target": tgt,
            "relation": relation,
            "confidence": confidence,
            "source_file": str_path,
            "source_location": f"L{line}",
            "weight": 1.0,
        }
        if context:
            edge["context"] = context
        edges.append(edge)

    file_nid = _make_id(str_path)
    add_node(file_nid, path.name, 1)

    try:
        cfscript_lang = Language(tscfml.language_cfscript())
        cfml_lang = Language(tscfml.language_cfml())
    except Exception as e:
        return {"nodes": [file_nid and nodes[0]] if nodes else [], "edges": [], "error": str(e)}

    is_tag_style = source.lstrip()[:1] == b"<"

    # label(lowercased) -> nid, for same-file call resolution
    local_functions: dict[str, str] = {}
    # (caller_nid, body_node, source_bytes, line_offset) — bodies walked after
    # all declarations are known, so forward references resolve.
    function_bodies: list[tuple[str, object, bytes, int]] = []

    # ------------------------------------------------------------------ script
    def handle_property(prop_node, src: bytes, owner_nid: str, line_offset: int) -> None:
        attrs = _component_attr_map(prop_node, src)
        name = attrs.get("name", "")
        inject = attrs.get("inject", "")
        if not inject:
            return
        # inject="delayedInjector:MyService" / "provider:x" / plain "MyService"
        service = inject.split(":", 1)[-1].strip()
        if not service:
            return
        line = prop_node.start_point[0] + 1 + line_offset
        stub = add_stub(service)
        add_edge(owner_nid, stub, "uses", line, context="wirebox_inject")
        if name:
            inject_map[name.lower()] = service

    def function_name(func_node, src: bytes) -> str | None:
        name_node = func_node.child_by_field_name("name")
        if name_node is not None:
            return _read_text(name_node, src)
        # `public any function init()` — return type parses as a bare
        # identifier; the function name is the last identifier before params.
        last = None
        for child in func_node.children:
            if child.type == "identifier":
                last = child
            elif child.type == "formal_parameters":
                break
        return _read_text(last, src) if last is not None else None

    def walk_script(node, src: bytes, owner_nid: str, line_offset: int) -> None:
        t = node.type
        if t == "component":
            attrs = _component_attr_map(node, src)
            line = node.start_point[0] + 1 + line_offset
            comp_name = path.stem
            comp_nid = _make_id(stem, comp_name)
            add_node(comp_nid, comp_name, line,
                     **({"cfml_extends": attrs["extends"]} if attrs.get("extends") else {}))
            add_edge(file_nid, comp_nid, "contains", line)
            extends = attrs.get("extends", "")
            if extends:
                base = extends.rsplit(".", 1)[-1]
                stub = add_stub(base, cfml_extends_path=extends)
                add_edge(comp_nid, stub, "inherits", line, context="extends")
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    walk_script(child, src, comp_nid, line_offset)
            return
        if t == "property_declaration":
            handle_property(node, src, owner_nid, line_offset)
            return
        if t == "function_declaration":
            name = function_name(node, src)
            if name:
                line = node.start_point[0] + 1 + line_offset
                func_nid = _make_id(stem, name) if owner_nid == file_nid else _make_id(owner_nid, name)
                add_node(func_nid, f"{name}()", line)
                relation = "contains" if owner_nid == file_nid else "method"
                add_edge(owner_nid, func_nid, relation, line)
                local_functions[name.lower()] = func_nid
                body = node.child_by_field_name("body")
                if body is not None:
                    function_bodies.append((func_nid, body, src, line_offset))
            return
        for child in node.children:
            walk_script(child, src, owner_nid, line_offset)

    def walk_calls(node, src: bytes, caller_nid: str, line_offset: int) -> None:
        if node.type == "function_declaration":
            return  # nested closures keep their own caller? keep it simple: stop
        if node.type == "call_expression":
            func_node = node.child_by_field_name("function")
            line = node.start_point[0] + 1 + line_offset
            if func_node is not None and func_node.type == "identifier":
                callee = _read_text(func_node, src)
                bare = callee.lstrip("$").lower()
                if bare in ("getpresideobject", "getpresideobjectservice"):
                    obj = _string_literal_arg(node, src)
                    if obj:
                        stub = add_stub(f"preside-object:{obj.lower()}")
                        pair = (caller_nid, stub)
                        if pair not in seen_call_pairs:
                            seen_call_pairs.add(pair)
                            add_edge(caller_nid, stub, "uses", line, context="preside_object")
                elif not _is_filtered_callee(callee):
                    tgt = local_functions.get(callee.lower())
                    if tgt and tgt != caller_nid:
                        pair = (caller_nid, tgt)
                        if pair not in seen_call_pairs:
                            seen_call_pairs.add(pair)
                            add_edge(caller_nid, tgt, "calls", line, context="call")
                    else:
                        raw_calls.append({
                            "caller_nid": caller_nid,
                            "callee": callee,
                            "is_member_call": False,
                            "source_file": str_path,
                            "source_location": f"L{line}",
                        })
            elif func_node is not None and func_node.type == "member_expression":
                obj_node = func_node.child_by_field_name("object")
                prop_node = func_node.child_by_field_name("property")
                if prop_node is not None:
                    callee = _read_text(prop_node, src)
                    receiver = ""
                    # `super` is its own node type in the grammar, not an identifier
                    if obj_node is not None and obj_node.type in ("identifier", "super"):
                        receiver = _read_text(obj_node, src)
                    bare_recv = receiver.lstrip("$").lower()
                    if receiver and bare_recv not in ("helpers", "this", "variables", "arguments", "event", "rc", "prc") \
                            and not _is_filtered_callee(callee):
                        rc: dict = {
                            "caller_nid": caller_nid,
                            "callee": callee,
                            "receiver": receiver,
                            "is_member_call": True,
                            "source_file": str_path,
                            "source_location": f"L{line}",
                        }
                        injected = inject_map.get(bare_recv)
                        if injected:
                            rc["injected_target"] = injected
                        if receiver == "super":
                            rc["is_super_call"] = True
                        raw_calls.append(rc)
        for child in node.children:
            walk_calls(child, src, caller_nid, line_offset)

    # --------------------------------------------------------------------- tag
    def tag_name_of(node, src: bytes) -> str:
        m = _TAG_NAME_RE.match(src[node.start_byte:node.end_byte])
        return m.group(1).decode("ascii", "replace").lower() if m else ""

    def walk_tag(root, src: bytes) -> None:
        # First pass: component + function open tags, flat by byte position.
        opens: list[tuple[int, int, str, dict, object]] = []  # (start, line, kind, attrs, node)
        component_nid: str | None = None

        def scan(node) -> None:
            nonlocal component_nid
            t = node.type
            if t == "cf_function_tag":
                attrs = _attr_map(node, src)
                opens.append((node.start_byte, node.start_point[0] + 1, "function", attrs, node))
            elif t.startswith("cf_") and t.endswith("_tag") and "close" not in t:
                name = tag_name_of(node, src)
                attrs = _attr_map(node, src)
                line = node.start_point[0] + 1
                if name == "component" and component_nid is None:
                    comp_name = path.stem
                    nid = _make_id(stem, comp_name)
                    add_node(nid, comp_name, line,
                             **({"cfml_extends": attrs["extends"]} if attrs.get("extends") else {}))
                    add_edge(file_nid, nid, "contains", line)
                    component_nid = nid
                    extends = attrs.get("extends", "")
                    if extends:
                        base = extends.rsplit(".", 1)[-1]
                        stub = add_stub(base, cfml_extends_path=extends)
                        add_edge(nid, stub, "inherits", line, context="extends")
                elif name == "include":
                    template = attrs.get("template", "")
                    if template:
                        stub = add_stub(Path(template).name or template,
                                        cfml_include_template=template)
                        add_edge(file_nid, stub, "references", line, context="cfinclude")
                elif name == "query":
                    qname = attrs.get("name", "")
                    if qname:
                        qnid = _make_id(stem, "query", qname)
                        add_node(qnid, f"query:{qname}", line)
                        add_edge(file_nid, qnid, "contains", line, context="cfquery")
                elif name == "property":
                    # tag-style <cfproperty name=".." inject="..">
                    inject = attrs.get("inject", "")
                    pname = attrs.get("name", "")
                    if inject:
                        service = inject.split(":", 1)[-1].strip()
                        if service:
                            owner = component_nid or file_nid
                            stub = add_stub(service)
                            add_edge(owner, stub, "uses", line, context="wirebox_inject")
                            if pname:
                                inject_map[pname.lower()] = service
            for child in node.children:
                scan(child)

        scan(root)

        # Function ranges: open tag -> matching </cffunction> (functions do not
        # nest in CFML), falling back to the next function open / EOF.
        owner = component_nid or file_nid
        ranges: list[tuple[int, int, str]] = []  # (start, end, func_nid)
        for i, (start, line, _kind, attrs, node) in enumerate(opens):
            name = attrs.get("name") or f"function_L{line}"
            func_nid = _make_id(owner, name) if owner != file_nid else _make_id(stem, name)
            add_node(func_nid, f"{name}()", line)
            add_edge(owner, func_nid, "method" if owner != file_nid else "contains", line)
            local_functions[name.lower()] = func_nid
            # A well-formed cf_function_tag spans its whole body including the
            # close tag; an unclosed one (grammar recovered) is open-tag only,
            # so fall forward to its close tag or the next function open.
            if _END_FUNCTION_RE.search(src[node.start_byte:node.end_byte]):
                end = node.end_byte
            else:
                m = _END_FUNCTION_RE.search(src, node.end_byte)
                end = m.end() if m else len(src)
                if i + 1 < len(opens):
                    end = min(end, opens[i + 1][0])
            ranges.append((start, end, func_nid))

        def owner_at(byte_pos: int) -> str:
            for start, end, func_nid in ranges:
                if start <= byte_pos < end:
                    return func_nid
            return file_nid

        # Second pass: expressions the cfml grammar parsed inline (hash
        # expressions, cfset bodies) plus <cfscript> blocks re-parsed with the
        # cfscript grammar.
        def scan_calls(node) -> None:
            if node.type == "call_expression":
                walk_calls(node, src, owner_at(node.start_byte), 0)
                return  # walk_calls recurses into children itself
            if node.type == "cf_script_content":
                content = src[node.start_byte:node.end_byte]
                try:
                    inner_tree = Parser(cfscript_lang).parse(content)
                except Exception:
                    return
                caller = owner_at(node.start_byte)
                offset = node.start_point[0]
                # declarations inside cfscript blocks (rare in views) and calls
                walk_script(inner_tree.root_node, content, caller if caller != file_nid else file_nid, offset)
                walk_calls(inner_tree.root_node, content, caller, offset)
                return
            for child in node.children:
                scan_calls(child)

        scan_calls(root)

    # ------------------------------------------------------------------- parse
    try:
        if is_tag_style:
            tree = Parser(cfml_lang).parse(source)
            walk_tag(tree.root_node, source)
        else:
            tree = Parser(cfscript_lang).parse(source)
            walk_script(tree.root_node, source, file_nid, 0)
    except Exception as e:
        return {"nodes": nodes, "edges": [], "error": str(e)}

    for caller_nid, body_node, src, line_offset in function_bodies:
        walk_calls(body_node, src, caller_nid, line_offset)

    valid_ids = seen_ids
    clean_edges = [e for e in edges
                   if e["source"] in valid_ids and e["target"] in valid_ids]

    return {"nodes": nodes, "edges": clean_edges, "raw_calls": raw_calls}
