"""Clean-error paths for missing files and invalid line numbers."""

import pytest

from lambdapi_mcp import tools


def test_check_missing_file(lsp, tmp_path):
    path = str(tmp_path / "nope.lp")
    r = tools.tool_check(lsp, path)
    assert r["ok"] is False
    assert r["error"] == "file not found"
    assert r["file"] == path


def test_symbols_missing_file(lsp, tmp_path):
    r = tools.tool_symbols(lsp, str(tmp_path / "nope.lp"))
    assert r["ok"] is False
    assert r["error"] == "file not found"


def test_goals_missing_file(lsp, tmp_path):
    r = tools.tool_goals(lsp, str(tmp_path / "nope.lp"), line=1)
    assert r["ok"] is False
    assert r["error"] == "file not found"


def test_hover_missing_file(lsp, tmp_path):
    r = tools.tool_hover(lsp, str(tmp_path / "nope.lp"), line=1, character=0)
    assert r["ok"] is False


def test_goals_invalid_line(lsp, fixture_path):
    # proof.lp has ~13 lines; line 10000 must return a clean error
    for bad in (0, -1, 10000):
        r = tools.tool_goals(lsp, fixture_path("proof.lp"), line=bad)
        assert r["ok"] is False, f"expected error for line={bad}, got {r}"
        assert "out of range" in r["error"]


def test_try_invalid_line(lsp, fixture_path):
    r = tools.tool_try(
        lsp, fixture_path("proof.lp"), line=0, tactic="reflexivity"
    )
    assert r["ok"] is False
    assert "out of range" in r["error"]


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
    # Good file still scanned
    names = {a["name"] for a in r["assumptions"]}
    assert "Nat" in names
    # Missing file surfaced in read_errors
    assert "read_errors" in r
    assert any("missing.lp" in e.get("file", "") for e in r["read_errors"])
