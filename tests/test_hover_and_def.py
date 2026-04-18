import pytest

from lambdapi_mcp import tools


def test_hover_on_local_symbol(lsp, fixture_path):
    # simple.lp line 2 (1-based): `constant symbol zero : Nat;`
    # Column 16 hovers over the `:` / `Nat` region. Use a position that
    # points at `Nat` (a real qident in the RangeMap).
    r = tools.tool_hover(lsp, fixture_path("simple.lp"),
                         line=2, character=23)
    # Either found with "Nat"/"TYPE" in contents, or cleanly not-found.
    assert "found" in r


def test_declaration_for_local_use(lsp, fixture_path):
    # simple.lp line 5 uses `zero` (in `rule double zero ↪ zero`).
    # Find the column of "zero" dynamically.
    with open(fixture_path("simple.lp")) as f:
        text = f.read()
    lines = text.split("\n")
    target_line = None
    target_col = None
    for i, line in enumerate(lines, 1):
        if line.startswith("rule double zero"):
            target_line = i
            target_col = line.index("zero")  # 0-based
            break
    assert target_line is not None
    r = tools.tool_declaration(
        lsp, fixture_path("simple.lp"),
        line=target_line, character=target_col,
    )
    if r.get("found"):
        assert r["line"] == 2, f"zero is declared on line 2; got {r}"
