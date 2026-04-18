from lambdapi_mcp import tools


def test_symbols_listed(lsp, fixture_path):
    r = tools.tool_symbols(lsp, fixture_path("simple.lp"))
    names = {s["name"] for s in r["symbols"]}
    for expected in ("Nat", "zero", "succ", "double"):
        assert expected in names, f"{expected} missing from {names}"


def test_symbols_have_line_numbers(lsp, fixture_path):
    r = tools.tool_symbols(lsp, fixture_path("simple.lp"))
    for s in r["symbols"]:
        assert s["line"] >= 1, f"line must be 1-based, got {s}"
