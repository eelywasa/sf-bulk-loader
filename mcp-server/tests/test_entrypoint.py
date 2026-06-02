"""Tests for the console-script entrypoint arg handling (SFBL-372 / SFBL-373).

The operator docs tell users to verify an install with `sf-bulk-loader-mcp
--help` and `--version`. These tests ensure those flags actually work (and do
NOT start the stdio server), so the documented verification step is real.
"""
from __future__ import annotations

import pytest

from sf_bulk_loader_mcp import server


def test_help_flag_prints_help_and_exits(monkeypatch, capsys) -> None:
    monkeypatch.setattr(server.sys, "argv", ["sf-bulk-loader-mcp", "--help"])
    # Must NOT call asyncio.run / start the server.
    monkeypatch.setattr(
        server.asyncio,
        "run",
        lambda *_a, **_k: pytest.fail("server should not start for --help"),
    )
    server.main()
    out = capsys.readouterr().out
    assert "sf-bulk-loader-mcp" in out
    assert "AUTH_MODE" in out
    assert "BULKLOADER_PAT" in out


def test_version_flag_prints_version_and_exits(monkeypatch, capsys) -> None:
    monkeypatch.setattr(server.sys, "argv", ["sf-bulk-loader-mcp", "--version"])
    monkeypatch.setattr(
        server.asyncio,
        "run",
        lambda *_a, **_k: pytest.fail("server should not start for --version"),
    )
    server.main()
    out = capsys.readouterr().out.strip()
    assert out.startswith("sf-bulk-loader-mcp ")
    # Version string is non-empty (resolved from package metadata).
    assert len(out.split()) == 2


def test_short_version_flag(monkeypatch, capsys) -> None:
    monkeypatch.setattr(server.sys, "argv", ["sf-bulk-loader-mcp", "-V"])
    monkeypatch.setattr(
        server.asyncio,
        "run",
        lambda *_a, **_k: pytest.fail("server should not start for -V"),
    )
    server.main()
    assert capsys.readouterr().out.strip().startswith("sf-bulk-loader-mcp ")


def test_no_args_starts_server(monkeypatch) -> None:
    """With no flags, main() starts the stdio server (asyncio.run is called)."""
    monkeypatch.setattr(server.sys, "argv", ["sf-bulk-loader-mcp"])
    called = {"ran": False}

    def _fake_run(coro=None, *_a, **_k) -> None:
        called["ran"] = True
        # Close the un-awaited coroutine to avoid a RuntimeWarning.
        if hasattr(coro, "close"):
            coro.close()

    monkeypatch.setattr(server.asyncio, "run", _fake_run)
    server.main()
    assert called["ran"] is True
