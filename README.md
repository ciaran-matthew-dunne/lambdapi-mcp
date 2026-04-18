# lambdapi-mcp

An [MCP](https://modelcontextprotocol.io/) server exposing
[Lambdapi](https://github.com/Deducteam/lambdapi) proof-assistant
capabilities to AI agents.

`lambdapi-mcp` is a thin layer on top of Lambdapi's standard LSP server:
each tool is implemented by composing LSP requests, so any Lambdapi
that ships `lambdapi lsp` works as a backend.

## Tools

| Tool                    | Purpose                                             |
| ----------------------- | --------------------------------------------------- |
| `lambdapi_check`        | Type-check a file; return first error if any        |
| `lambdapi_goals`        | Proof state (hyps + goals) at a 1-based line        |
| `lambdapi_query`        | Run `compute` / `type` / `print` / `search` at a line |
| `lambdapi_try`          | Try a tactic at a line without modifying the file   |
| `lambdapi_multi_try`    | Try several tactics in parallel                     |
| `lambdapi_symbols`      | List symbols declared in a file                     |
| `lambdapi_axioms`       | Scan files for axioms, postulates, and admits       |
| `lambdapi_hover`        | Type info at a (line, character) position           |
| `lambdapi_declaration`  | Jump to the file + line where a symbol is declared  |
| `lambdapi_completions`  | In-scope symbol and tactic completions at a position |

All positions exposed to tools use **1-based lines and 0-based columns**,
matching how users think about source files.

## Install

```bash
pip install lambdapi-mcp
```

Requires:

- Python 3.10+
- A `lambdapi` binary on PATH (or passed via `--binary`)
- The Lambdapi Stdlib for tools that exercise proofs (automatically
  picked up from the opam installation)

## Use

### From Claude Desktop / other MCP clients

Add to your MCP config (for Claude Desktop: `~/.config/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "lambdapi": {
      "command": "lambdapi-mcp"
    }
  }
}
```

Optional flags:

- `--lib-root PATH` — pass through as `--lib-root` to `lambdapi lsp`
- `--stdlib PATH` — add as `--map-dir Stdlib:PATH` to `lambdapi lsp`
- `--binary PATH` — explicit path to the `lambdapi` binary

### Directly

```bash
lambdapi-mcp
```

Speaks MCP on stdio; typically you don't invoke it by hand.

## Design

`lambdapi-mcp` matches the design of
[`lean-lsp-mcp`](https://github.com/oOo0oOo/lean-lsp-mcp) and
[`rocq-mcp`](https://github.com/LLM4Rocq/rocq-mcp) — all three layer on
top of the proof assistant's LSP server rather than re-implementing the
check loop, so they track upstream improvements for free.

For probing-style tools (`query`, `try`, `multi_try`), the server
modifies the document text in-memory and re-issues `textDocument/didOpen`
with the modified content, then reads back the resulting diagnostics and
goals. The file on disk is never touched.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Fixtures live in `tests/fixtures/`. Tests that require the Lambdapi
Stdlib are skipped automatically if it isn't installed.

## License

Apache-2.0.
