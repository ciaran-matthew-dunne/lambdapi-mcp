"""Rewrite rules are assumptions too — tool_axioms must surface them."""

import pytest

from lambdapi_mcp import tools


def test_rewrite_rules_picked_up_from_simple(lsp, fixture_path):
    """simple.lp defines `double` with a `rule … with …` block:

        rule double zero ↪ zero
        with double (succ $n) ↪ succ (succ (double $n));

    Both sub-rules must be listed, attributed to `double`."""
    r = tools.tool_axioms(lsp, [fixture_path("simple.lp")])
    rr = r["rewrite_rules"]
    assert len(rr) == 2, f"expected 2 rules, got {len(rr)}: {rr}"
    assert all(x["symbol"] == "double" for x in rr), (
        f"expected all rules to have symbol='double': {rr}"
    )
    lhs_rhs = {(x["lhs"], x["rhs"]) for x in rr}
    assert ("double zero", "zero") in lhs_rhs
    # Second rule: `double (succ $n) ↪ succ (succ (double $n))`
    assert any("double (succ $n)" == l for l, _ in lhs_rhs)


def test_rewrite_rules_empty_when_none(lsp, fixture_path):
    """proof.lp has no `rule` declarations locally."""
    r = tools.tool_axioms(lsp, [fixture_path("proof.lp")])
    # Even if transitively required files have rules, we only assert
    # the shape: the field must always be present and a list.
    assert isinstance(r["rewrite_rules"], list)
    local = [x for x in r["rewrite_rules"] if x["file"].endswith("proof.lp")]
    assert local == [], f"proof.lp has no rewrite rules, got {local}"
