"""Pre/post goal consistency and the closed/progress flags for try."""

import pathlib

from lambdapi_mcp import tools


def test_try_pre_goals_are_populated_for_closing_tactic(
    lsp, fixture_path, require_stdlib
):
    """Regression: `reflexivity` used to report pre_goals=[] because the
    LSP was queried after the closing tactic had already collapsed the
    proof state. Pre-state must reflect the goal *before* the tactic."""
    r = tools.tool_try(
        lsp, fixture_path("proof.lp"),
        line=5, tactic="reflexivity", mode="replace",
    )
    assert r["ok"], r
    assert len(r["pre_goals"]) == 1, (
        f"pre_goals should show the open goal, got {r['pre_goals']}"
    )
    assert len(r["post_goals"]) == 0
    assert r["closed"] is True
    assert r["progress"] is True


def test_try_symmetry_no_progress_flagged(lsp, fixture_path, require_stdlib):
    """`symmetry` on `π (0 = 0)` leaves the state unchanged — progress
    must be False."""
    r = tools.tool_try(
        lsp, fixture_path("proof.lp"),
        line=5, tactic="symmetry", mode="replace",
    )
    assert r["ok"] is True
    assert r["progress"] is False
    assert r["closed"] is False


def test_try_closed_flag_false_on_error(lsp, fixture_path, require_stdlib):
    r = tools.tool_try(
        lsp, fixture_path("proof.lp"),
        line=5, tactic="apply nonexistent_symbol", mode="replace",
    )
    assert r["ok"] is False
    assert r["closed"] is False
    assert r["progress"] is False


def test_try_does_not_touch_file_on_disk(lsp, fixture_path, require_stdlib):
    path = pathlib.Path(fixture_path("proof.lp"))
    before = path.read_bytes()
    tools.tool_try(lsp, str(path), line=5, tactic="reflexivity")
    tools.tool_try(lsp, str(path), line=5, tactic="admit", mode="replace")
    tools.tool_multi_try(
        lsp, str(path), line=5,
        tactics=["reflexivity", "symmetry", "admit"],
    )
    assert path.read_bytes() == before, "try/multi_try must not mutate the file"
