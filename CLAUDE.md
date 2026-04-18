# lambdapi-mcp

MCP server exposing Lambdapi proof-assistant capabilities to AI agents. Layers on top of `lambdapi lsp`; source in `src/lambdapi_mcp/`, pytest suite in `tests/` with fixtures in `tests/fixtures/`.

## Architecture

- `lsp.py` — JSON-RPC client for `lambdapi lsp`. Handles framing, request/reply routing, diagnostics. Auto-restarts the subprocess on `BrokenPipeError` / death; `restart_count` is observable.
- `tools.py` — pure functions taking an `LSPClient`, returning JSON-serialisable dicts. Every tool validates its inputs (`_check_file`, `_check_line`) and returns `{ok: false, error: ...}` on bad input rather than leaking Python exceptions.
- `server.py` — `FastMCP` glue; registers one MCP tool per function in `tools.py` and holds a single long-lived `LSPClient` per session.
- `__main__.py` — CLI (`lambdapi-mcp`) with `--lib-root`, `--stdlib`, `--binary`, `--log-file`.

## Tools (10)

`lambdapi_check`, `lambdapi_goals`, `lambdapi_hover`, `lambdapi_declaration`, `lambdapi_symbols`, `lambdapi_query`, `lambdapi_try`, `lambdapi_multi_try`, `lambdapi_completions`, `lambdapi_axioms`.

Notable semantics worth remembering:

- **`lambdapi_try` / `multi_try`**: probe in-memory only (file on disk is never touched). Returns `pre_goals` / `post_goals` plus three booleans: `ok` (no error diagnostic on the probe line), `closed` (pre had ≥1 goal, post has 0 → proof obligation finished), `progress` (goal state actually changed — compared gid-free). `ok` alone does NOT mean the tactic did anything useful.
- **`lambdapi_symbols`**: the upstream lambdapi LSP leaks transitively-imported symbols with the queried URI and original-file line numbers. We filter against a local declaration parse of the file — only symbols that actually appear as `symbol NAME` / `inductive NAME` / etc. in the input file are returned.
- **`lambdapi_axioms`**: transitive. Follows `require` / `require open` through the full package graph (resolved against lib_root + map_dirs + any nested `lambdapi.pkg`). Returns `assumptions` (any `symbol` without `≔` body, flagged `propositional` iff type is `π …`), `rewrite_rules` (every `rule LHS ↪ RHS` including sub-rules of `with`-chained blocks), `admits`, plus `scanned_files` and `unresolved_imports`. Statement-level scan (splits on top-level `;`) so multi-line declarations with bodies on later lines are correctly recognised as definitions, not assumptions.

## Local Lambdapi corpora

Real Lambdapi source trees on this machine — **use these when exercising the MCP tools manually**, instead of writing throwaway `.lp` files:

- `~/prog/lambdapi` — the Lambdapi proof assistant itself; includes `tests/OK/`, `tests/KO/`, `tests/regressions/`, `libraries/`.
- `~/prog/lambdapi-stdlib` — standard library (`Bool.lp`, `Nat.lp`, `List.lp`, `Eq.lp`, `Prop.lp`, `Set.lp`, `Tactic.lp`, `Z.lp`, …).
- `~/prog/hyperset/lp` — larger project (ZFC + AFA) with a nested package (`lp/lambdapi.pkg` + `lp/ZF/lambdapi.pkg`); good stress test for transitive axiom scanning (27 files, 93 assumptions, 8 rewrite rules starting from `Axioms.lp`).

The `lambdapi` binary is at `~/.opam/default/bin/lambdapi`. The opam-installed Stdlib lives under `~/.opam/default/lib/lambdapi/lib_root/Stdlib`. `.lpo` files next to `.lp` are derived caches — safe to `rm` when lambdapi versions skew (they'll regenerate).

The `tests/fixtures/` dir in this repo is for the pytest suite — don't expand it for ad-hoc tool exploration.

## Running

- Tests: `source .venv/bin/activate && python -m pytest tests/ -v` (36 tests, ~9 s).
- Python 3.13 venv at `.venv/`; deps managed via `pyproject.toml` (mcp>=1.0.0, pytest>=8.0 as dev).
- The crash-recovery test (`tests/test_crash_recovery.py`) SIGKILLs the underlying `lambdapi lsp` subprocess — harmless, but expect a brief zombie reap.
