"""MADPANDA3D Discord MCP package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mad-mcp-discord")
except PackageNotFoundError:  # pragma: no cover - source checkout before install
    __version__ = "0+unknown"

__all__ = ["__version__"]
