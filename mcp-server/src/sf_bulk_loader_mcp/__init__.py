"""Salesforce Bulk Loader MCP server package."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the installed package metadata, which the release
    # workflow stamps from the git tag (release.yml injects the version into
    # pyproject.toml before build). Avoids drift between a hardcoded literal and
    # the published version.
    __version__ = version("sf-bulk-loader-mcp")
except PackageNotFoundError:  # pragma: no cover - source tree without dist metadata
    __version__ = "0.0.0+unknown"
