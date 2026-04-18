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
    uri = file_uri(file)
    text = _read(file)
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
    probe = _ensure_semicolon(query)
    text = _read(file)
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
    overwrites [line] (useful when probing an already-bound name)."""
    if mode not in ("insert", "replace"):
        return {"ok": False, "error": f"bad mode {mode!r}"}
    probe = _ensure_semicolon(tactic)
    text = _read(file)
    if mode == "insert":
        modified = _insert_at(text, line, probe)
        probe_line_0 = line - 1
        original_line = None
    else:
        modified, original_line = _replace_line(text, line, probe)
        probe_line_0 = line - 1

    uri = file_uri(file)
    client.did_open(uri, modified)
    try:
        # First: get goals just before the probe line (= pre-state).
        pre = client.goals(uri, line=probe_line_0, character=0) or {}
        # Drain didOpen's diagnostics.
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
    result = {
        "file": file,
        "line": line,
        "tactic": tactic,
        "mode": mode,
        "pre_goals": pre.get("goals", []),
        "post_goals": post.get("goals", []),
    }
    if mode == "replace":
        result["replaced_line"] = original_line
    if errs_at_probe:
        result["ok"] = False
        result["error"] = errs_at_probe[0]["message"]
    else:
        result["ok"] = True
    return result


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
    outcomes = [
        tool_try(client, file, line, t, mode=mode) for t in tactics
    ]
    return {"file": file, "line": line, "attempts": outcomes}


# --- lambdapi_symbols -------------------------------------------------


def tool_symbols(client: LSPClient, file: str) -> dict:
    """List the symbols declared in [file] via textDocument/documentSymbol."""
    uri = file_uri(file)
    text = _read(file)
    client.did_open(uri, text)
    try:
        client.drain_notifications(timeout=5.0)
        result = client.document_symbol(uri) or []
    finally:
        client.did_close(uri)
    symbols = []
    for s in result:
        loc = s.get("location", {})
        rng = loc.get("range", {}).get("start", {})
        symbols.append({
            "name": s.get("name", ""),
            "kind": s.get("kind"),
            "line": rng.get("line", 0) + 1,
            "character": rng.get("character", 0),
        })
    return {"file": file, "symbols": symbols}


# --- lambdapi_axioms --------------------------------------------------


# Parser-like regexes for shape classification. Run line-by-line; good
# enough for the common cases (axioms + postulates + admits).
_AXIOM_RE = re.compile(
    r"^\s*(?:private\s+|protected\s+)?"
    r"constant\s+symbol\s+([^\s:]+)\s*:\s*π\b",
)
_POSTULATE_RE = re.compile(
    r"^\s*(?:private\s+|protected\s+|sequential\s+|injective\s+)?"
    r"(?:constant\s+)?symbol\s+([^\s:]+)\s*:\s*(.+?)\s*;?\s*$",
)
_ADMIT_RE = re.compile(r"^\s*admit\s*;")


def tool_axioms(client: LSPClient, files: list[str]) -> dict:
    """Scan the given files for axioms, postulates, and admits.

    - *Axiom*: ``constant symbol X : π ...;`` (a propositional assumption).
    - *Postulate*: ``symbol X : T;`` or ``constant symbol X : T;`` where
      the type is not a proposition and there is no ``≔`` definition.
    - *Admit*: a proof containing ``admit;``.
    """
    axioms: list[dict] = []
    postulates: list[dict] = []
    admits: list[dict] = []
    for f in files:
        text = _read(f)
        for i, line in enumerate(_split_lines(text), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            if "≔" in stripped or ":=" in stripped:
                # Has a definition body: not axiomatic.
                continue
            m = _AXIOM_RE.match(stripped)
            if m:
                axioms.append({"file": f, "line": i, "name": m.group(1)})
                continue
            m = _POSTULATE_RE.match(stripped)
            if m and "π" not in m.group(2):
                # Conservative: only call it a postulate if it's a
                # symbol-of-a-type that isn't a proof obligation.
                postulates.append(
                    {"file": f, "line": i, "name": m.group(1)}
                )
        for i, line in enumerate(_split_lines(text), 1):
            if _ADMIT_RE.match(line):
                admits.append({"file": f, "line": i})
    return {
        "files": files,
        "axioms": axioms,
        "postulates": postulates,
        "admits": admits,
    }


# --- lambdapi_hover ---------------------------------------------------


def tool_hover(client: LSPClient, file: str, line: int, character: int) -> dict:
    """Return hover information at (1-based [line], 0-based [character])."""
    uri = file_uri(file)
    text = _read(file)
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
    uri = file_uri(file)
    text = _read(file)
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
    uri = file_uri(file)
    text = _read(file)
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
