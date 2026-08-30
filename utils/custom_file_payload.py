"""Backwards-compatible import path.

The canonical location is ``blaze_hammer/ext/attachments.py``; edit that
file to configure files sent via ``--file-payload``.
"""

from blaze_hammer.ext.attachments import attachments  # noqa: F401

__all__ = ["attachments"]
