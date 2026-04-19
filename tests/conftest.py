"""pytest fixtures: one LSP client per test, a temp lib_root populated
from the fixture .lp files."""

from __future__ import annotations

import os
import pathlib
import shutil
import tempfile

import pytest

from lambdapi_mcp.lsp import LSPClient, default_lib_root


HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"


def _local_binary() -> str | None:
    """Prefer a locally-built `lambdapi` next to this checkout if one
    exists (useful when developing against a pre-publish branch)."""
    candidates = [
        "../lambdapi/_build/install/default/bin/lambdapi",
        "./_build/install/default/bin/lambdapi",
    ]
    for c in candidates:
        abs_ = os.path.abspath(os.path.join(HERE.parent, c))
        if os.path.isfile(abs_):
            return abs_
    return shutil.which("lambdapi")


@pytest.fixture
def lib_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Per-test lib root seeded with the fixture .lp files."""
    for f in FIXTURES.glob("*.lp"):
        (tmp_path / f.name).write_text(f.read_text())
    (tmp_path / "lambdapi.pkg").write_text(
        "package_name = test\nroot_path = test\n"
    )
    return tmp_path


@pytest.fixture
def stdlib() -> str | None:
    stdlib = os.path.join(default_lib_root(), "Stdlib")
    return stdlib if os.path.isdir(stdlib) else None


@pytest.fixture
def lsp(lib_root: pathlib.Path, stdlib: str | None):
    """A running LSPClient with stdlib mapped if available."""
    map_dirs: list[str] = []
    if stdlib:
        map_dirs.append(f"Stdlib:{stdlib}")
    client = LSPClient(
        lib_root=str(lib_root),
        map_dirs=map_dirs,
        binary=_local_binary(),
    )
    client.start()
    try:
        yield client
    finally:
        client.stop()


@pytest.fixture
def fixture_path(lib_root: pathlib.Path):
    def _get(name: str) -> str:
        return str(lib_root / name)
    return _get


@pytest.fixture
def require_stdlib(stdlib):
    """Autoskip if the opam-installed Stdlib is missing. Depend on this
    fixture in tests that need `Stdlib.*` imports to resolve."""
    if not stdlib:
        pytest.skip("Stdlib required")
