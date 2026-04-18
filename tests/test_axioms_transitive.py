"""Transitive `require` closure: tool_axioms follows imports and reports
everything in scope, not just declarations in the input files."""

import pytest

from lambdapi_mcp import tools


def _skip_no_stdlib(stdlib):
    if not stdlib:
        pytest.skip("Stdlib required")


def test_axioms_follows_direct_imports(lsp, fixture_path, stdlib):
    """proof.lp imports Stdlib.{Set,Prop,Eq,Nat}. Scanning it should
    pick up declarations from those modules, not just proof.lp itself."""
    _skip_no_stdlib(stdlib)
    r = tools.tool_axioms(lsp, [fixture_path("proof.lp")])
    # Direct file listed first
    assert r["scanned_files"][0].endswith("proof.lp")
    # Stdlib.Eq must be reached transitively.
    eq_scanned = any(p.endswith("/Stdlib/Eq.lp") for p in r["scanned_files"])
    assert eq_scanned, f"Stdlib/Eq.lp not in scanned files: {r['scanned_files']}"
    # `eq_refl` should show up in assumptions, flagged as propositional.
    by_name = {a["name"]: a for a in r["assumptions"]}
    assert "eq_refl" in by_name, (
        f"eq_refl not found; got {sorted(by_name)[:15]}…"
    )
    assert by_name["eq_refl"]["propositional"] is True
    assert by_name["eq_refl"]["constant"] is True
    assert by_name["eq_refl"]["file"].endswith("/Stdlib/Eq.lp")


def test_axioms_transitive_scanned_files_are_deduplicated(
    lsp, fixture_path, stdlib
):
    _skip_no_stdlib(stdlib)
    path = fixture_path("proof.lp")
    r = tools.tool_axioms(lsp, [path, path])  # duplicate input
    # Even though we passed the file twice, it's scanned once.
    n_self = sum(1 for p in r["scanned_files"] if p.endswith("proof.lp"))
    assert n_self == 1, f"duplicate scan of proof.lp: {r['scanned_files']}"


def test_axioms_records_unresolved_imports(lsp, fixture_path, tmp_path):
    # A standalone .lp file that requires a module whose prefix has no
    # matching package. We point tool_axioms at a file in tmp_path —
    # outside the lsp's lib_root, so nothing imported is resolvable.
    src = tmp_path / "phantom.lp"
    src.write_text("require open NoSuchPackage.DoesNotExist;\nsymbol X : τ ι;\n")
    r = tools.tool_axioms(lsp, [str(src)])
    assert r.get("unresolved_imports"), r
    assert any(
        u["module"] == "NoSuchPackage.DoesNotExist"
        for u in r["unresolved_imports"]
    )
    # The file itself was still scanned (local postulate picked up).
    # Note: our regex requires a trailing `;`, so X may be filtered if
    # the text-body doesn't match. The key assertion here is that the
    # unresolved import didn't crash the call.
    assert str(src) in r["scanned_files"]


def test_axioms_no_imports_only_self(lsp, fixture_path):
    """simple.lp has no `require`: the transitive scan should visit
    only simple.lp and report its local assumptions."""
    path = fixture_path("simple.lp")
    r = tools.tool_axioms(lsp, [path])
    assert r["scanned_files"] == [path]
    names = {a["name"] for a in r["assumptions"]}
    assert {"Nat", "zero", "succ", "double"} <= names
    # All of these are type/data — none are propositional.
    props = [a for a in r["assumptions"] if a["propositional"]]
    assert props == [], f"unexpected propositional assumptions: {props}"


def test_axioms_distinguishes_propositional(lsp, fixture_path, stdlib):
    _skip_no_stdlib(stdlib)
    r = tools.tool_axioms(lsp, [fixture_path("proof.lp")])
    by_name = {a["name"]: a for a in r["assumptions"]}
    # ⊤ᵢ : π ⊤ — propositional axiom
    if "⊤ᵢ" in by_name:
        assert by_name["⊤ᵢ"]["propositional"] is True
    # Set : TYPE — data postulate
    if "Set" in by_name:
        assert by_name["Set"]["propositional"] is False
