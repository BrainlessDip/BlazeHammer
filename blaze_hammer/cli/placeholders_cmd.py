"""``placeholders`` command — documentation generated from the registry."""

from __future__ import annotations

import click

from blaze_hammer.errors import EXIT_OK


@click.command(name="placeholders")
@click.argument("query", required=False, metavar="[NAME|faker [SUBQUERY]]")
def placeholders(query: str | None) -> None:
    """List built-in placeholders; 'placeholders faker' browses Faker methods."""
    from blaze_hammer.services import placeholders_docs

    code = placeholders_docs(query)
    if code != EXIT_OK:
        raise SystemExit(code)
