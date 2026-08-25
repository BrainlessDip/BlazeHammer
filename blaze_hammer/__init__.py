"""Blaze Hammer — asynchronous API testing and traffic generation."""

from importlib.metadata import PackageNotFoundError, version

APP_NAME = "Blaze Hammer"

try:
    __version__: str = version("blazehammer")
except PackageNotFoundError:  # pragma: no cover - running from a non-installed tree
    __version__ = "0.0.0.dev0"

__all__ = ["APP_NAME", "__version__"]
