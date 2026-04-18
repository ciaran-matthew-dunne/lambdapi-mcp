"""MCP server: tool registration + lifecycle.

Each MCP tool maps to a function in ``tools.py`` and is registered with
FastMCP. The server holds a single ``LSPClient`` for the session so that
subsequent tool calls reuse the already-checked state where possible.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from . import tools as T
from .lsp import LSPClient, default_lib_root


def build_server(
    lib_root: str | None = None,
    stdlib: str | None = None,
    binary: str | None = None,
) -> FastMCP:
    lib_root = lib_root or default_lib_root()
    map_dirs: list[str] = []
    if stdlib and os.path.isdir(stdlib):
        map_dirs.append(f"Stdlib:{stdlib}")
    elif stdlib is None:
        # Auto-pick the opam Stdlib if present.
        default = os.path.expanduser(
            "~/.opam/default/lib/lambdapi/lib_root/Stdlib"
        )
        if os.path.isdir(default):
            map_dirs.append(f"Stdlib:{default}")

    lsp = LSPClient(lib_root=lib_root, map_dirs=map_dirs, binary=binary)
    lsp.start()

    mcp = FastMCP("lambdapi-mcp")

    @mcp.tool(
        description="Type-check a Lambdapi (.lp) file. Returns ok=true on "
                    "success, or ok=false with the sorted list of errors."
    )
    def lambdapi_check(file: str) -> dict:
        return T.tool_check(lsp, file)

    @mcp.tool(
        description="Return the proof state (hypotheses and goals) at a "
                    "1-based line in a Lambdapi file."
    )
    def lambdapi_goals(file: str, line: int) -> dict:
        return T.tool_goals(lsp, file, line)

    @mcp.tool(
        description="Run a Lambdapi query (compute/type/print/search) at a "
                    "given 1-based line. Output is returned as a string."
    )
    def lambdapi_query(file: str, line: int, query: str) -> dict:
        return T.tool_query(lsp, file, line, query)

    @mcp.tool(
        description="Try a tactic at a line without modifying the file. "
                    "mode='insert' (default) prepends the tactic; "
                    "mode='replace' overwrites the line, useful when the "
                    "tactic introduces a name that would clash."
    )
    def lambdapi_try(
        file: str, line: int, tactic: str, mode: str = "insert"
    ) -> dict:
        return T.tool_try(lsp, file, line, tactic, mode=mode)

    @mcp.tool(
        description="Try multiple tactics at the same line in parallel. "
                    "Returns a list of per-tactic outcomes (same shape as "
                    "lambdapi_try)."
    )
    def lambdapi_multi_try(
        file: str, line: int, tactics: list[str], mode: str = "insert"
    ) -> dict:
        return T.tool_multi_try(lsp, file, line, tactics, mode=mode)

    @mcp.tool(
        description="List the symbols declared in a file (via LSP "
                    "documentSymbol)."
    )
    def lambdapi_symbols(file: str) -> dict:
        return T.tool_symbols(lsp, file)

    @mcp.tool(
        description="Scan files for unproved assumptions — axioms, "
                    "postulates, and admits."
    )
    def lambdapi_axioms(files: list[str]) -> dict:
        return T.tool_axioms(lsp, files)

    @mcp.tool(
        description="Hover information at a (line, character) position. "
                    "Returns the type of the hovered symbol."
    )
    def lambdapi_hover(file: str, line: int, character: int) -> dict:
        return T.tool_hover(lsp, file, line, character)

    @mcp.tool(
        description="Go-to-definition: returns the file and line where the "
                    "symbol at the given position is declared."
    )
    def lambdapi_declaration(
        file: str, line: int, character: int
    ) -> dict:
        return T.tool_declaration(lsp, file, line, character)

    @mcp.tool(
        description="List completion suggestions at a position (in-scope "
                    "symbols + tactic keywords inside proofs). Requires a "
                    "lambdapi with the completion patch."
    )
    def lambdapi_completions(
        file: str, line: int, character: int
    ) -> dict:
        return T.tool_completions(lsp, file, line, character)

    # Attach the LSP handle for tests / introspection.
    mcp._lsp_client = lsp  # type: ignore[attr-defined]
    return mcp
