import pytest

from lambdapi_mcp import tools


def _skip_no_stdlib(stdlib):
    if not stdlib:
        pytest.skip("Stdlib required")


def test_try_reflexivity_closes(lsp, fixture_path, stdlib):
    _skip_no_stdlib(stdlib)
    # zero_eq_zero is proved by `reflexivity` on line 5 of proof.lp.
    # Probe `reflexivity;` by replacing that line with itself: the
    # behaviour should be "ok, post has 0 goals" (proof closes).
    r = tools.tool_try(
        lsp, fixture_path("proof.lp"),
        line=5, tactic="reflexivity", mode="replace",
    )
    assert r["ok"], r
    assert len(r["post_goals"]) == 0, f"reflexivity should close: {r}"


def test_try_bogus_tactic_fails(lsp, fixture_path, stdlib):
    _skip_no_stdlib(stdlib)
    r = tools.tool_try(
        lsp, fixture_path("proof.lp"),
        line=5, tactic="apply nonexistent_symbol", mode="replace",
    )
    assert r["ok"] is False
    assert r.get("error")


def test_multi_try_returns_all_attempts(lsp, fixture_path, stdlib):
    _skip_no_stdlib(stdlib)
    r = tools.tool_multi_try(
        lsp, fixture_path("proof.lp"),
        line=5,
        tactics=["reflexivity", "simplify"],
        mode="replace",
    )
    assert len(r["attempts"]) == 2
    assert r["attempts"][0]["tactic"] == "reflexivity"
    assert r["attempts"][1]["tactic"] == "simplify"
