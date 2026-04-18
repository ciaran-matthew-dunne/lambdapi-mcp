"""MCP tool implementations.

Each tool is a plain function that takes an ``LSPClient`` and returns
a JSON-serialisable dict. Tools never talk to the LSP server directly —
they compose requests via the client, which keeps the MCP layer a thin
shell over standard LSP.
"""

from __future__ import annotations

import os
import re

from .lsp import LSPClient, LSPError, file_uri


# --- Small helpers ----------------------------------------------------


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _check_file(path: str) -> dict | None:
    """Return a clean error dict if [path] can't be read, else None."""
    if not isinstance(path, str) or not path:
        return {"ok": False, "error": "file: expected non-empty string"}
    if not os.path.isfile(path):
        return {"ok": False, "file": path, "error": "file not found"}
    if not os.access(path, os.R_OK):
        return {"ok": False, "file": path, "error": "file not readable"}
    return None


def _check_line(text: str, line: int) -> dict | None:
    """Return a clean error dict if 1-based [line] is out of [text]'s range."""
    if not isinstance(line, int):
        return {"ok": False, "error": "line: expected int"}
    n = len(_split_lines(text))
    if line < 1 or line > n + 1:
        return {
            "ok": False,
            "error": f"line {line} out of range: file has {n} line(s) "
                     f"(valid: 1..{n + 1})",
        }
    return None


def _split_lines(text: str) -> list[str]:
    return text.split("\n")


def _join_lines(lines: list[str]) -> str:
    return "\n".join(lines)


def _ensure_semicolon(s: str) -> str:
    s = s.rstrip()
    return s if s.endswith(";") else s + ";"


def _insert_at(text: str, line_1based: int, content: str) -> str:
    """Insert [content] as its own line before 1-based [line_1based]."""
    lines = _split_lines(text)
    lines.insert(line_1based - 1, content)
    return _join_lines(lines)


def _replace_line(text: str, line_1based: int, content: str) -> tuple[str, str]:
    """Replace the 1-based [line_1based] with [content].
    Returns (new_text, original_line_stripped)."""
    lines = _split_lines(text)
    original = lines[line_1based - 1]
    lines[line_1based - 1] = content
    return _join_lines(lines), original.strip()


def _errors(diags: list[dict]) -> list[dict]:
    return [d for d in diags if d.get("severity") == 1]


def _format_err(d: dict) -> dict:
    r = d.get("range", {}).get("start", {})
    return {
        "line": r.get("line", 0) + 1,    # 1-based for humans
        "character": r.get("character", 0),
        "message": d.get("message", ""),
    }


# --- lambdapi_check ---------------------------------------------------


def tool_check(client: LSPClient, file: str) -> dict:
    """Type-check a .lp file. Returns OK or the first error."""
    err = _check_file(file)
    if err:
        return err
    uri = file_uri(file)
    text = _read(file)
    client.did_open(uri, text)
    try:
        diags = client.latest_diagnostics(
            client.drain_notifications(timeout=5.0), uri=uri
        )
    finally:
        client.did_close(uri)
    errs = _errors(diags)
    if not errs:
        return {"ok": True, "file": file}
    errs.sort(
        key=lambda d: (
            d["range"]["start"]["line"], d["range"]["start"]["character"]
        )
    )
    return {"ok": False, "file": file, "errors": [_format_err(d) for d in errs]}


# --- lambdapi_goals ---------------------------------------------------


def tool_goals(client: LSPClient, file: str, line: int) -> dict:
    """Return the proof state (hyps + goals) at 1-based [line]."""
    err = _check_file(file)
    if err:
        return err
    text = _read(file)
    err = _check_line(text, line)
    if err:
        err["file"] = file
        err["line"] = line
        return err
    uri = file_uri(file)
    client.did_open(uri, text)
    try:
        client.drain_notifications(timeout=5.0)
        # proof/goals uses 0-based lines; column 0 is fine.
        result = client.goals(uri, line=line - 1, character=0)
    finally:
        client.did_close(uri)
    return {"file": file, "line": line, "state": result or {"goals": []}}


# --- lambdapi_query ---------------------------------------------------


_QUERY_VERBS = {"compute", "type", "print", "search"}


def tool_query(
    client: LSPClient, file: str, line: int, query: str
) -> dict:
    """Run a query at [line]. [query] is the full query text, e.g.
    ``compute (1 + 1)`` or ``print foo``."""
    verb = query.strip().split(None, 1)[0] if query.strip() else ""
    if verb not in _QUERY_VERBS:
        return {
            "ok": False,
            "error": f"unknown query verb {verb!r}; "
                     f"expected one of {sorted(_QUERY_VERBS)}",
        }
    err = _check_file(file)
    if err:
        return err
    text = _read(file)
    err = _check_line(text, line)
    if err:
        err["file"] = file
        err["line"] = line
        return err
    probe = _ensure_semicolon(query)
    modified = _insert_at(text, line, probe)
    uri = file_uri(file)
    client.did_open(uri, modified)
    try:
        # The LSP emits query output through window/logMessage and
        # through the OK-hint diagnostic's message field. Capture both.
        notifs = client.drain_notifications(timeout=5.0)
        diags = client.latest_diagnostics(notifs, uri=uri)
    finally:
        client.did_close(uri)
    errs = _errors(diags)
    if errs:
        return {"ok": False, "error": errs[0]["message"]}
    # The probe line is at [line] (1-based). Pick the OK-hint whose
    # range.start.line matches line-1 (0-based).
    target = line - 1
    hints = [
        d for d in diags
        if d.get("severity") == 4
        and d["range"]["start"]["line"] == target
    ]
    output = "\n".join(d["message"] for d in hints) or ""
    # Gather any window/logMessage notifications as additional output.
    logs = [
        m["params"].get("message", "")
        for m in notifs if m.get("method") == "window/logMessage"
    ]
    return {
        "ok": True,
        "file": file,
        "line": line,
        "query": query,
        "output": output,
        "logs": logs,
    }


# --- lambdapi_try -----------------------------------------------------


def tool_try(
    client: LSPClient,
    file: str,
    line: int,
    tactic: str,
    mode: str = "insert",
) -> dict:
    """Try a tactic at [line] without modifying the file on disk.

    ``mode='insert'`` inserts the tactic before [line]; ``mode='replace'``
    overwrites [line] (useful when probing an already-bound name).

    Returns ``ok`` (no error diagnostic on the probe line), plus:
    - ``closed``: the post-state has zero goals while the pre-state had ≥1
      (the tactic finished the proof obligation at that point),
    - ``progress``: the goal state changed (tactic did something).
    """
    if mode not in ("insert", "replace"):
        return {"ok": False, "error": f"bad mode {mode!r}"}
    if not isinstance(tactic, str) or not tactic.strip():
        return {"ok": False, "error": "tactic: expected non-empty string"}
    err = _check_file(file)
    if err:
        return err
    text = _read(file)
    err = _check_line(text, line)
    if err:
        err["file"] = file
        err["line"] = line
        return err
    probe = _ensure_semicolon(tactic)
    if mode == "insert":
        modified = _insert_at(text, line, probe)
        original_line = None
    else:
        modified, original_line = _replace_line(text, line, probe)
    probe_line_0 = line - 1

    uri = file_uri(file)

    # 1. Capture the pre-state from the UNMODIFIED document. The LSP's
    # reply at (probe_line_0, 0) would otherwise depend on whether the
    # probed tactic closed the proof — inserting `reflexivity` at a
    # closed-goal row, for example, makes the LSP return an empty
    # "pre-state". Querying the unmodified text sidesteps that.
    client.did_open(uri, text)
    try:
        client.drain_notifications(timeout=5.0)
        pre = client.goals(uri, line=probe_line_0, character=0) or {}
    finally:
        client.did_close(uri)

    # 2. Now probe the modified document for post-state + diagnostics.
    client.did_open(uri, modified)
    try:
        diags = client.latest_diagnostics(
            client.drain_notifications(timeout=5.0), uri=uri
        )
        post = client.goals(uri, line=probe_line_0 + 1, character=0) or {}
    finally:
        client.did_close(uri)

    errs_at_probe = [
        d for d in _errors(diags)
        if d["range"]["start"]["line"] == probe_line_0
    ]
    pre_goals = pre.get("goals", []) or []
    post_goals = post.get("goals", []) or []
    result = {
        "file": file,
        "line": line,
        "tactic": tactic,
        "mode": mode,
        "pre_goals": pre_goals,
        "post_goals": post_goals,
    }
    if mode == "replace":
        result["replaced_line"] = original_line
    if errs_at_probe:
        result["ok"] = False
        result["closed"] = False
        result["progress"] = False
        result["error"] = errs_at_probe[0]["message"]
    else:
        result["ok"] = True
        result["closed"] = bool(pre_goals) and not post_goals
        result["progress"] = _goals_key(pre_goals) != _goals_key(post_goals)
    return result


def _goals_key(goals: list[dict]) -> list[tuple]:
    """A gid-free, hashable summary of a goal list, for progress checks.

    The LSP assigns fresh goal ids on every didOpen, so `gid` differs
    between our pre- and post- probes even when the tactic made no
    change. Compare on (typeofgoal, type, normalised hyps) instead."""
    return [
        (
            g.get("typeofgoal", ""),
            g.get("type", ""),
            tuple(
                (h.get("hname", ""), h.get("htype", ""))
                for h in g.get("hyps", []) or []
            ),
        )
        for g in goals
    ]


# --- lambdapi_multi_try ----------------------------------------------


def tool_multi_try(
    client: LSPClient,
    file: str,
    line: int,
    tactics: list[str],
    mode: str = "insert",
) -> dict:
    """Try each tactic independently at [line]. Returns a list of
    per-tactic outcomes in the same shape as ``lambdapi_try``."""
    if not isinstance(tactics, list) or not tactics:
        return {
            "ok": False,
            "file": file,
            "line": line,
            "error": "tactics: expected a non-empty list of tactic strings",
        }
    outcomes = [
        tool_try(client, file, line, t, mode=mode) for t in tactics
    ]
    return {"file": file, "line": line, "attempts": outcomes}


# --- lambdapi_symbols -------------------------------------------------


_DECL_RE = re.compile(
    r"^\s*"
    # zero or more modifiers (in any order) before `symbol` / `inductive`
    r"(?:(?:opaque|private|protected|sequential|injective|constant)\s+)*"
    r"(?:symbol|inductive)\s+"
    # symbol name: anything up to whitespace or `:` or `[`
    r"([^\s:\[]+)"
)


def _local_decl_names(text: str) -> set[str]:
    """Parse [text] line-by-line for locally-declared symbol names.

    Handles `symbol NAME`, `constant symbol NAME`, `opaque symbol NAME`,
    `inductive NAME`, etc. Used to filter documentSymbol output, since
    the upstream lambdapi LSP leaks transitively-imported symbols into
    the reply."""
    names: set[str] = set()
    for line in _split_lines(text):
        m = _DECL_RE.match(line)
        if m:
            names.add(m.group(1))
    return names


def tool_symbols(client: LSPClient, file: str) -> dict:
    """List the symbols declared in [file] via textDocument/documentSymbol.

    The upstream lambdapi LSP replies with transitively-imported symbols
    attributed to the queried URI. We cross-check each reported symbol's
    name against a local declaration parse of [file] and drop anything
    that isn't actually declared in this file."""
    err = _check_file(file)
    if err:
        return err
    uri = file_uri(file)
    text = _read(file)
    local_names = _local_decl_names(text)
    client.did_open(uri, text)
    try:
        client.drain_notifications(timeout=5.0)
        result = client.document_symbol(uri) or []
    finally:
        client.did_close(uri)
    symbols = []
    for s in result:
        name = s.get("name", "")
        if name not in local_names:
            continue
        loc = s.get("location", {})
        rng = loc.get("range", {}).get("start", {})
        symbols.append({
            "name": name,
            "kind": s.get("kind"),
            "line": rng.get("line", 0) + 1,
            "character": rng.get("character", 0),
        })
    return {"file": file, "symbols": symbols}


# --- lambdapi_axioms --------------------------------------------------


# Parser-like regexes for shape classification. Run line-by-line; good
# enough for the common cases (axioms + postulates + admits).
# Binders look like `[x y : τ a]` or `(x : τ a)`; zero or more may sit
# between the symbol name and its `:` type annotation.
_BINDERS = r"(?:\s*\[[^\]]*\]|\s*\([^)]*\))*"

# Any ``symbol`` / ``constant symbol`` declaration, captured on one line.
# Groups: 1=constant?, 2=name, 3=type (up to `;` / EOL, excluding any body).
_SYMBOL_DECL_RE = re.compile(
    r"^\s*(?:private\s+|protected\s+|sequential\s+|injective\s+|opaque\s+)*"
    r"(constant\s+)?symbol\s+([^\s:\[\(]+)" + _BINDERS +
    r"\s*:\s*(.+?)\s*;?\s*$",
)
_ADMIT_RE = re.compile(r"^\s*admit\s*;")

_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_REQUIRE_RE = re.compile(
    r"\brequire\b(?:\s+open\b)?\s+(.+?);",
    re.DOTALL,
)
_MODULE_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


def _read_pkg(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, _, v = line.partition("=")
                    out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _discover_pkg_roots(
    lib_root: str | None, map_dirs: list[str]
) -> dict[str, str]:
    """Return ``{root_path_name: directory}`` for every known Lambdapi
    package — map_dirs plus any ``lambdapi.pkg`` found under lib_root."""
    roots: dict[str, str] = {}
    for md in map_dirs or []:
        if ":" in md:
            name, path = md.split(":", 1)
            if os.path.isdir(path):
                roots.setdefault(name, path)
    if lib_root and os.path.isdir(lib_root):
        for dirpath, _dirnames, filenames in os.walk(lib_root):
            if "lambdapi.pkg" in filenames:
                pkg = _read_pkg(os.path.join(dirpath, "lambdapi.pkg"))
                rp = pkg.get("root_path")
                if rp:
                    roots.setdefault(rp, dirpath)
    return roots


def _resolve_module(module: str, roots: dict[str, str]) -> str | None:
    """Resolve ``Stdlib.Nat`` → ``/.../Stdlib/Nat.lp``."""
    parts = module.split(".")
    if not parts:
        return None
    prefix = parts[0]
    root_dir = roots.get(prefix)
    if root_dir is None:
        return None
    rel = os.path.join(*parts[1:]) + ".lp" if len(parts) > 1 else prefix + ".lp"
    path = os.path.join(root_dir, rel)
    return path if os.path.isfile(path) else None


def _parse_requires(text: str) -> list[str]:
    """Return the module names mentioned in any ``require ... ;`` block."""
    stripped = _LINE_COMMENT_RE.sub("", text)
    stripped = _BLOCK_COMMENT_RE.sub("", stripped)
    modules: list[str] = []
    for m in _REQUIRE_RE.finditer(stripped):
        for tok in _MODULE_TOKEN_RE.findall(m.group(1)):
            modules.append(tok)
    return modules


def _strip_comments(text: str) -> str:
    """Remove `// …` and `/* … */` comments while preserving newlines
    so line numbers stay aligned."""
    out = _BLOCK_COMMENT_RE.sub(
        lambda m: re.sub(r"[^\n]", " ", m.group(0)), text
    )
    out = _LINE_COMMENT_RE.sub("", out)
    return out


def _split_statements(text: str) -> list[tuple[int, str]]:
    """Split [text] (with comments already stripped) into statements
    terminated by a top-level ``;``. Returns (start_line_1based, body)
    pairs with the original line of each statement's first character."""
    stmts: list[tuple[int, str]] = []
    buf: list[str] = []
    depth = 0
    line = 1
    stmt_start: int | None = None
    for ch in text:
        if ch not in " \t\n" and stmt_start is None:
            stmt_start = line
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == ";" and depth == 0:
            body = "".join(buf).strip()
            if body and stmt_start is not None:
                stmts.append((stmt_start, body))
            buf.clear()
            stmt_start = None
        else:
            buf.append(ch)
        if ch == "\n":
            line += 1
    # Any unterminated tail is ignored (malformed file).
    return stmts


_RULE_STMT_RE = re.compile(r"^\s*rule\b(.+)$", re.DOTALL)
_RULE_HEAD_RE = re.compile(r"^\s*([^\s\(\[]+)")


def _parse_rewrite_rules(body: str) -> list[tuple[str, str, str]]:
    """Split a `rule …[with …]*` body into ``(head, lhs, rhs)`` triples.

    ``head`` is the leftmost identifier on the LHS — the symbol this
    rule reduces. ``lhs`` and ``rhs`` are the raw text on either side
    of ``↪``."""
    out: list[tuple[str, str, str]] = []
    # Statements are split at top-level `;`, so we never see `with` from
    # outside a rule here. Splitting on word-boundary `with` is safe.
    subs = re.split(r"\bwith\b", body)
    for sub in subs:
        if "↪" not in sub:
            continue
        lhs, _, rhs = sub.partition("↪")
        lhs = lhs.strip()
        rhs = rhs.strip()
        m = _RULE_HEAD_RE.match(lhs)
        head = m.group(1) if m else ""
        out.append((head, lhs, rhs))
    return out


def _scan_assumptions(
    f: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Classify declarations in a single file.

    Returns ``(assumptions, rewrite_rules, admits)``.

    - **assumption**: any ``symbol`` / ``constant symbol`` declared
      without a ``≔`` body — something the theory takes on faith.
      ``propositional`` marks propositional types (the classical
      "logical axioms" like ``em``, ``eq_refl``).
    - **rewrite_rule**: a ``rule LHS ↪ RHS;`` declaration (or any
      sub-rule of a ``rule … with … with …;`` block). Rewrite rules
      are assumptions too: they extend reduction, and their
      termination / confluence is not automatically checked.
    - **admit**: an ``admit;`` tactic inside a proof (a hole).
    """
    assumptions: list[dict] = []
    rewrite_rules: list[dict] = []
    admits: list[dict] = []
    raw = _read(f)
    text = _strip_comments(raw)
    for start_line, stmt in _split_statements(text):
        # Rewrite rules: `rule LHS ↪ RHS [with …]*`
        m = _RULE_STMT_RE.match(stmt)
        if m:
            for head, lhs, rhs in _parse_rewrite_rules(m.group(1)):
                rewrite_rules.append({
                    "file": f,
                    "line": start_line,
                    "symbol": head,
                    "lhs": " ".join(lhs.split()),
                    "rhs": " ".join(rhs.split()),
                })
            continue
        if "≔" in stmt or ":=" in stmt:
            continue  # has a definition body → not an assumption
        single = " ".join(stmt.split())
        dm = _SYMBOL_DECL_RE.match(single)
        if not dm:
            continue
        is_constant = bool(dm.group(1))
        name = dm.group(2)
        type_str = dm.group(3).strip()
        assumptions.append({
            "file": f,
            "line": start_line,
            "name": name,
            "type": type_str,
            "propositional": _is_propositional(type_str),
            "constant": is_constant,
        })
    for i, line in enumerate(_split_lines(raw), 1):
        if _ADMIT_RE.match(line):
            admits.append({"file": f, "line": i})
    return assumptions, rewrite_rules, admits


def _is_propositional(type_str: str) -> bool:
    """A type is propositional iff it eventually applies ``π`` to a Prop
    (i.e. ``π …`` somewhere at the top level after quantifiers). We
    approximate: a leading token ``π`` or ``Π …, π`` counts."""
    s = type_str.lstrip()
    if s.startswith("π"):
        return True
    # `Π x:A, π B` — a dependent function returning a proposition.
    # Also handles `∀`-sugar forms.
    return bool(re.search(r"(?:^|\s|,)π[\s(]", type_str))


def tool_axioms(client: LSPClient, files: list[str]) -> dict:
    """Scan the given files — and everything they transitively ``require``
    — for unproved assumptions.

    Three categories are returned:

    - **assumptions**: any ``symbol`` / ``constant symbol`` declared
      without a ``≔`` body — the theory takes its meaning on faith.
      Each entry: ``name``, ``file``, ``line``, ``type`` (declared type
      as text), ``propositional`` (true iff the type is a proposition,
      i.e. a ``π …``), and ``constant`` (whether the ``constant``
      modifier was used). Filter ``propositional=True`` for the usual
      logical axioms (``em``, ``eq_refl``, ``⊤ᵢ``); the rest are
      type/data postulates (``Set : TYPE``, ``ι : Set``, …).
    - **rewrite_rules**: every ``rule LHS ↪ RHS;`` (including each
      sub-rule in a ``rule … with … with …;`` block). Rewrite rules
      are assumptions too — they extend reduction, and their
      termination / confluence isn't automatically checked. Each entry:
      ``symbol`` (the head of the LHS being reduced), ``lhs``, ``rhs``,
      ``file``, ``line``.
    - **admits**: every ``admit;`` tactic inside a proof (a hole).

    Imports are followed via ``require`` / ``require open`` statements,
    resolved against the LSP client's ``lib_root`` and ``map_dirs``.
    ``scanned_files`` lists every file visited. Modules that can't be
    resolved are reported under ``unresolved_imports`` rather than
    failing the whole call."""
    if not isinstance(files, list) or any(
        not isinstance(f, str) for f in files
    ):
        return {
            "ok": False,
            "error": "files: expected a list of file-path strings",
        }

    roots = _discover_pkg_roots(
        getattr(client, "lib_root", None),
        getattr(client, "map_dirs", []) or [],
    )

    assumptions: list[dict] = []
    rewrite_rules: list[dict] = []
    admits: list[dict] = []
    read_errors: list[dict] = []
    unresolved: list[dict] = []

    scanned: set[str] = set()
    scan_order: list[str] = []
    frontier: list[tuple[str, str | None]] = []
    for f in files:
        err = _check_file(f)
        if err:
            read_errors.append(err)
            continue
        frontier.append((os.path.abspath(f), None))

    while frontier:
        path, imported_by = frontier.pop(0)
        if path in scanned:
            continue
        if not os.path.isfile(path):
            read_errors.append({
                "ok": False, "file": path, "error": "file not found",
                "imported_by": imported_by,
            })
            continue
        scanned.add(path)
        scan_order.append(path)
        a, rr, ad = _scan_assumptions(path)
        assumptions.extend(a)
        rewrite_rules.extend(rr)
        admits.extend(ad)
        text = _read(path)
        for mod in _parse_requires(text):
            resolved = _resolve_module(mod, roots)
            if resolved is None:
                unresolved.append({"module": mod, "imported_by": path})
                continue
            resolved_abs = os.path.abspath(resolved)
            if resolved_abs not in scanned:
                frontier.append((resolved_abs, path))

    result = {
        "files": files,
        "scanned_files": scan_order,
        "assumptions": assumptions,
        "rewrite_rules": rewrite_rules,
        "admits": admits,
    }
    if read_errors:
        result["read_errors"] = read_errors
    if unresolved:
        result["unresolved_imports"] = unresolved
    return result


# --- lambdapi_hover ---------------------------------------------------


def tool_hover(client: LSPClient, file: str, line: int, character: int) -> dict:
    """Return hover information at (1-based [line], 0-based [character])."""
    err = _check_file(file)
    if err:
        return err
    text = _read(file)
    err = _check_line(text, line)
    if err:
        err["file"] = file
        err["line"] = line
        err["character"] = character
        return err
    if not isinstance(character, int) or character < 0:
        return {"ok": False, "file": file, "line": line,
                "error": f"character {character} must be a non-negative int"}
    uri = file_uri(file)
    client.did_open(uri, text)
    try:
        client.drain_notifications(timeout=5.0)
        result = client.hover(uri, line=line - 1, character=character)
    finally:
        client.did_close(uri)
    if result is None:
        return {"file": file, "line": line, "character": character,
                "found": False}
    contents = result.get("contents")
    if isinstance(contents, dict):
        text_content = contents.get("value", "")
    elif isinstance(contents, list):
        text_content = "\n".join(
            c.get("value", "") if isinstance(c, dict) else str(c)
            for c in contents
        )
    else:
        text_content = str(contents or "")
    return {
        "file": file, "line": line, "character": character,
        "found": True,
        "contents": text_content,
    }


# --- lambdapi_declaration --------------------------------------------


def tool_declaration(
    client: LSPClient, file: str, line: int, character: int
) -> dict:
    """Return the declaration location of the symbol at the given
    position, via textDocument/definition."""
    err = _check_file(file)
    if err:
        return err
    text = _read(file)
    err = _check_line(text, line)
    if err:
        err["file"] = file
        err["line"] = line
        err["character"] = character
        return err
    if not isinstance(character, int) or character < 0:
        return {"ok": False, "file": file, "line": line,
                "error": f"character {character} must be a non-negative int"}
    uri = file_uri(file)
    client.did_open(uri, text)
    try:
        client.drain_notifications(timeout=5.0)
        result = client.definition(uri, line=line - 1, character=character)
    finally:
        client.did_close(uri)
    loc = None
    if isinstance(result, dict):
        loc = result
    elif isinstance(result, list) and result:
        loc = result[0]
    if loc is None:
        return {"file": file, "line": line, "character": character,
                "found": False}
    target_uri = loc.get("uri", "")
    rng = loc.get("range", {}).get("start", {})
    return {
        "found": True,
        "file": target_uri[7:] if target_uri.startswith("file://")
                else target_uri,
        "line": rng.get("line", 0) + 1,
        "character": rng.get("character", 0),
    }


# --- lambdapi_completions --------------------------------------------


def tool_completions(
    client: LSPClient, file: str, line: int, character: int
) -> dict:
    """List completion suggestions at the given position.

    Requires the server to advertise ``completionProvider`` — available
    when ``lambdapi lsp`` is on a branch with the completion patch."""
    err = _check_file(file)
    if err:
        return err
    text = _read(file)
    err = _check_line(text, line)
    if err:
        err["file"] = file
        err["line"] = line
        err["character"] = character
        return err
    if not isinstance(character, int) or character < 0:
        return {"ok": False, "file": file, "line": line,
                "error": f"character {character} must be a non-negative int"}
    uri = file_uri(file)
    client.did_open(uri, text)
    try:
        client.drain_notifications(timeout=5.0)
        try:
            result = client.request("textDocument/completion", {
                "textDocument": {"uri": uri},
                "position": {"line": line - 1, "character": character},
            })
        except LSPError as e:
            return {"file": file, "supported": False, "error": str(e)}
    finally:
        client.did_close(uri)
    items = (result or {}).get("items", []) if isinstance(result, dict) else []
    return {
        "file": file,
        "supported": True,
        "items": [
            {
                "label": i.get("label", ""),
                "kind": i.get("kind"),
                "detail": i.get("detail", ""),
            }
            for i in items
        ],
    }
