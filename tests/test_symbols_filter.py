"""Cross-file leakage regression: tool_symbols must not return symbols
whose line is outside the queried file's range."""

import pytest

from lambdapi_mcp import tools


def _skip_no_stdlib(stdlib):
    if not stdlib:
        pytest.skip("Stdlib required for proof.lp imports")


def test_symbols_filter_imported_out_of_range(lsp, fixture_path, stdlib):
    _skip_no_stdlib(stdlib)
    path = fixture_path("proof.lp")
    r = tools.tool_symbols(lsp, path)
    # Count lines in the actual file.
    n_lines = len(open(path).read().split("\n"))
    for s in r["symbols"]:
        assert 1 <= s["line"] <= n_lines, (
            f"symbol {s['name']!r} reported at line {s['line']} "
            f"but {path} only has {n_lines} lines"
        )


def test_symbols_only_locally_declared(lsp, fixture_path, stdlib):
    """proof.lp declares `zero_eq_zero` and `eq_sym_nat` — those should
    be present, and imports like `ℕ`, `𝔹`, `+1` should be filtered."""
    _skip_no_stdlib(stdlib)
    r = tools.tool_symbols(lsp, fixture_path("proof.lp"))
    names = {s["name"] for s in r["symbols"]}
    assert "zero_eq_zero" in names
    assert "eq_sym_nat" in names
    # These live in imported Stdlib modules at lines > 13.
    for imported in ("ℕ", "𝔹", "+1"):
        assert imported not in names, (
            f"{imported!r} leaked from imports; full set: {sorted(names)[:20]}"
        )
