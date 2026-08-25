"""``init`` command — create a Blaze Hammer project directory."""

from __future__ import annotations

from pathlib import Path

import click


@click.command(name="init")
@click.argument("project_name", required=False, metavar="[PROJECT_NAME]")
@click.option("--name", "name_option", default=None, help="Project name (named form).")
@click.option(
    "--path",
    "parent_dir",
    type=click.Path(path_type=str),
    default=None,
    help="Parent directory for the project (default: current directory).",
)
@click.option("--target", default=None, help="Initial target URL.")
@click.option(
    "-m",
    "--method",
    type=click.Choice(["GET", "POST"], case_sensitive=False),
    default=None,
    help="HTTP method (default: POST).",
)
@click.option("-n", "--requests", type=int, default=None, help="Request count (default: 100).")
@click.option("-c", "--concurrency", type=int, default=None, help="Concurrency (default: 10).")
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help="Create everything with defaults; never prompt.",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite an existing project directory.",
)
def init(
    project_name: str | None,
    name_option: str | None,
    parent_dir: str | None,
    target: str | None,
    method: str | None,
    requests: int | None,
    concurrency: int | None,
    non_interactive: bool,
    force: bool,
) -> None:
    """Create a new Blaze Hammer project in ./<PROJECT_NAME>/."""
    from blaze_hammer.services import init_project

    def validated(value: str | None) -> str | None:
        """Render invalid-name errors like every other CLI failure."""
        if value is None:
            return None
        from blaze_hammer.errors import BlazeHammerError
        from blaze_hammer.output.console import plain_error
        from blaze_hammer.services import validate_project_name

        try:
            return validate_project_name(value)
        except BlazeHammerError as error:
            click.echo(plain_error(error))
            raise SystemExit(error.exit_code) from None

    # Name conflict rule: positional and --name must agree when both given.
    if project_name and name_option and project_name.strip() != name_option.strip():
        raise click.ClickException(
            "Conflicting project names\n\n"
            f"  positional: {project_name}\n"
            f"  --name:     {name_option}\n\n"
            "Provide only one of the two."
        )
    # Validate BEFORE touching the filesystem so traversal ('../evil',
    # 'a/b', '.') can never normalize into a different target directory.
    name = validated(project_name or name_option)
    parent = Path(parent_dir) if parent_dir else Path.cwd()

    console_target = target
    console_method = (method or "POST").upper()
    console_requests = requests or 100
    console_concurrency = concurrency or 10
    create_examples = True

    interactive = not non_interactive
    if interactive:
        click.echo("Blaze Hammer - Initialize Project\n")
        if not name:
            name = validated(click.prompt("Project name", type=str))
        if console_target is None:
            console_target = (
                click.prompt("Target URL (optional)", default="https://example.com/api").strip()
                or "https://example.com/api"
            )
        console_method = click.prompt(
            "HTTP method",
            type=click.Choice(["GET", "POST"], case_sensitive=False),
            default=console_method,
        ).upper()
        console_requests = click.prompt("Requests", type=int, default=console_requests)
        console_concurrency = click.prompt("Concurrency", type=int, default=console_concurrency)
        create_examples = click.confirm("Create example payload/headers?", default=True)
        click.echo()
    elif not name:
        raise click.ClickException(
            "Project name required with --non-interactive\n\n"
            "  bh init my-api-test --non-interactive"
        )
    if not name:  # pragma: no cover - narrowed above; keeps mypy precise
        raise click.ClickException("Project name required")

    try:
        created = init_project(
            root=parent / name,
            target=console_target or "https://example.com/api",
            method=console_method,
            requests=console_requests,
            concurrency=console_concurrency,
            create_examples=create_examples,
            force=force,
        )
    except Exception as error:  # noqa: BLE001 - rendered below via BlazeHammerError path
        from blaze_hammer.errors import BlazeHammerError
        from blaze_hammer.output.console import plain_error

        if isinstance(error, BlazeHammerError):
            click.echo(plain_error(error))
            raise SystemExit(error.exit_code) from None
        raise

    click.echo(f"Created {name}/")
    for item in created:
        if item:
            click.echo(f"  Created {item}")
    click.echo("\nNext steps:\n")
    click.echo(f"  cd {name}")
    click.echo("  bh validate")
    click.echo("  bh run")
