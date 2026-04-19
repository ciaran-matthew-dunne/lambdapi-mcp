from lambdapi_mcp import tools


def test_goals_outside_proof_is_empty(lsp, fixture_path, require_stdlib):
    r = tools.tool_goals(lsp, fixture_path("proof.lp"), line=1)
    goals = (r["state"] or {}).get("goals") or []
    assert goals == [], f"expected empty goals outside proof, got {goals}"


def test_goals_inside_proof(lsp, fixture_path, require_stdlib):
    # Line 10 in proof.lp is `symmetry;` inside eq_sym_nat.
    r = tools.tool_goals(lsp, fixture_path("proof.lp"), line=10)
    goals = (r["state"] or {}).get("goals") or []
    assert len(goals) >= 1, f"expected ≥1 goal at line 10, got {goals}"
