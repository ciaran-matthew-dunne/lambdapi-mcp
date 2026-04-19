"""Clean-error paths for missing files and invalid line numbers."""

import pytest

from lambdapi_mcp import tools


# ----- missing-file -------------------------------------------------------

MISSING_FILE_CALLS = {
    "check":    lambda lsp, p: tools.tool_check(lsp, p),
    "symbols":  lambda lsp, p: tools.tool_symbols(lsp, p),
    "goals":    lambda lsp, p: tools.tool_goals(lsp, p, line=1),
    "hover":    lambda lsp, p: tools.tool_hover(lsp, p, line=1, character=0),
    "decl":     lambda lsp, p: tools.tool_declaration(lsp, p, line=1, character=0),
    "query":    lambda lsp, p: tools.tool_query(lsp, p, line=1, query="type x"),
    "try":      lambda lsp, p: tools.tool_try(lsp, p, line=1, tactic="reflexivity"),
    "completions": lambda lsp, p: tools.tool_completions(lsp, p, line=1, character=0),
}


@pytest.mark.parametrize("tool_name", sorted(MISSING_FILE_CALLS))
def test_tool_missing_file_returns_clean_error(lsp, tmp_path, tool_name):
    path = str(tmp_path / "nope.lp")
    r = MISSING_FILE_CALLS[tool_name](lsp, path)
    assert r["ok"] is False
    assert r["error"] == "file not found"


# ----- invalid line -------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, 10000])
def test_goals_invalid_line(lsp, fixture_path, bad):
    r = tools.tool_goals(lsp, fixture_path("proof.lp"), line=bad)
    assert r["ok"] is False
    assert "out of range" in r["error"]


def test_try_invalid_line(lsp, fixture_path):
    r = tools.tool_try(
        lsp, fixture_path("proof.lp"), line=0, tactic="reflexivity"
    )
    assert r["ok"] is False
    assert "out of range" in r["error"]


# ----- bad arguments ------------------------------------------------------


def test_try_empty_tactic(lsp, fixture_path):
    r = tools.tool_try(lsp, fixture_path("proof.lp"), line=5, tactic="")
    assert r["ok"] is False
    assert "non-empty" in r["error"]


def test_multi_try_empty_list(lsp, fixture_path):
    r = tools.tool_multi_try(
        lsp, fixture_path("proof.lp"), line=5, tactics=[]
    )
    assert r["ok"] is False


def test_axioms_rejects_non_list(lsp, fixture_path):
    r = tools.tool_axioms(lsp, fixture_path("simple.lp"))  # string, not list
    assert r["ok"] is False
    assert "list" in r["error"]


def test_axioms_collects_read_errors(lsp, fixture_path, tmp_path):
    r = tools.tool_axioms(
        lsp,
        [fixture_path("simple.lp"), str(tmp_path / "missing.lp")],
    )
    names = {a["name"] for a in r["assumptions"]}
    assert "Nat" in names  # good file still scanned
    assert "read_errors" in r
    assert any("missing.lp" in e.get("file", "") for e in r["read_errors"])
