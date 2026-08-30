"""``faker`` command — discover and inspect Faker providers/methods."""

from __future__ import annotations

import click

from blaze_hammer.errors import EXIT_OK


@click.group(name="faker")
def faker_group() -> None:
    """Browse available Faker providers and inspect methods."""


@faker_group.command(name="list")
@click.argument("query", required=False, default="")
@click.option("--locale", default=None, help="Filter by Faker locale.")
def faker_list(query: str, locale: str | None) -> None:
    """List available Faker providers and methods.

    Examples::

        blaze-hammer faker list
        blaze-hammer faker list email
        blaze-hammer faker list --locale bn_BD
    """
    from blaze_hammer.services import faker_list_providers

    code = faker_list_providers(query=query, locale=locale)
    if code != EXIT_OK:
        raise SystemExit(code)


@faker_group.command(name="show")
@click.argument("method")
@click.option("--locale", default=None, help="Use a specific Faker locale.")
def faker_show(method: str, locale: str | None) -> None:
    """Show details for a Faker method (signature, docs, example).

    Examples::

        blaze-hammer faker show random_int
        blaze-hammer faker show name --locale bn_BD
    """
    from blaze_hammer.services import faker_show_method

    code = faker_show_method(method=method, locale=locale)
    if code != EXIT_OK:
        raise SystemExit(code)
