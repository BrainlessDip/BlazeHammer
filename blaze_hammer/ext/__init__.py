"""User-editable extension surface.

These modules are the supported places to customize Blaze Hammer by
editing code:

- ``parsers``   – per-status-code output formatting (``custom_*_parsers`` dicts)
- ``providers`` – custom Faker providers (auto-registered at startup)
- ``attachments`` – static file attachments for ``--file-payload``

The legacy ``utils/custom_parsers.py``, ``utils/custom_providers.py`` and
``utils/custom_file_payload.py`` paths still work as import shims.
"""
