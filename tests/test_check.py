from lambdapi_mcp import tools


def test_check_clean_file(lsp, fixture_path):
    r = tools.tool_check(lsp, fixture_path("simple.lp"))
    assert r["ok"], r
    assert r["file"].endswith("simple.lp")


def test_check_reports_error(lsp, fixture_path):
    r = tools.tool_check(lsp, fixture_path("with_error.lp"))
    assert r["ok"] is False
    assert r["errors"]
    first = r["errors"][0]
    assert "Undefined" in first["message"]
    # 1-based line: the error is on line 3 of with_error.lp
    assert first["line"] == 3


def test_check_multiple_errors_sorted(lsp, fixture_path):
    r = tools.tool_check(lsp, fixture_path("multiple_errors.lp"))
    assert r["ok"] is False
    lines = [e["line"] for e in r["errors"]]
    assert lines == sorted(lines), f"errors should be sorted by line: {lines}"
