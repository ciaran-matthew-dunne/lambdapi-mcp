"""MCP server for the Lambdapi proof assistant.

Exposes Lambdapi's type-checking, proof-state, and symbol-query
capabilities to AI agents via the Model Context Protocol. All tools
are implemented on top of the standard ``lambdapi lsp`` server; no
changes to the Lambdapi codebase are required.
"""

__version__ = "0.1.0"
