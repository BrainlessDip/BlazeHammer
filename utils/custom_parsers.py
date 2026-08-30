"""Backwards-compatible import path.

The canonical location is ``blaze_hammer/ext/parsers.py``; edit that file.
This shim keeps ``utils.custom_parsers`` imports working.
"""  # noqa: A005

from blaze_hammer.ext.parsers import (  # noqa: F401
    custom_headers_parsers,
    custom_payload_parsers,
    custom_response_parsers,
    headers_parse,
    parse_200,
    payload_parse,
)
