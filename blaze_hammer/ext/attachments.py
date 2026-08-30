"""Static file attachments used with ``--file-payload``.

Populate ``attachments`` with either open file objects or filesystem
paths; paths are validated and opened lazily by
``blaze_hammer.files.attachments`` so nothing is loaded until a run
actually starts::

    attachments = {
        "upload": "example.jpg",          # path (preferred)
        # "upload": open("example.jpg", "rb"),  # file object also works
    }

The dictionary maps the multipart form *field name* to the file.
"""

attachments: dict[str, object] = {}
