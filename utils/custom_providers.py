"""Backwards-compatible import path.

The canonical location is ``blaze_hammer/ext/providers.py``; edit that file.
Any ``BaseProvider`` subclass defined there is auto-registered.
"""

from blaze_hammer.ext.providers import (  # noqa: F401
    AdvancedExampleProvider,
    SimpleExampleProvider,
)

__all__ = ["AdvancedExampleProvider", "SimpleExampleProvider"]
