"""Click application group, legacy routing, version and shell completion."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from blaze_hammer import __version__
from blaze_hammer.errors import EXIT_USAGE

SUBCOMMANDS = frozenset(
    {
        "run",
        "init",
        "web",
        "inspect",
        "validate",
        "placeholders",
        "profiles",
        "profile",
        "interactive",
        "version",
        "completion",
        "faker",
    }
)


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "-V", "--version", prog_name="blaze-hammer")
def cli() -> None:
    """Blaze Hammer - asynchronous API testing and traffic generation.

    \b
        Usage:
          bh [OPTIONS] [URL]
          bh run [OPTIONS] [URL]

    \b
        Examples:
          bh                                    run project (blazehammer.yaml)
          bh https://api.example.com/users      override target only
          bh https://api.example.com/users -n 1000 -c 50   target + overrides
          bh init                               create a project
          bh validate / inspect / --dry-run     preflight checks

        [URL] overrides the target configured in blazehammer.yaml. Run
        'blaze-hammer placeholders' for placeholder docs.
    """


@cli.command(name="version")
def version() -> None:
    """Print the Blaze Hammer version."""
    click.echo(f"blaze-hammer {__version__}")


_COMPLETION_SHELLS = ("bash", "zsh", "fish", "powershell")


@cli.command(name="completion")
@click.argument("shell", type=click.Choice(_COMPLETION_SHELLS))
def completion(shell: str) -> None:
    """Print shell completion script (bash/zsh/fish/powershell)."""
    from click.shell_completion import get_completion_class

    completion_cls = get_completion_class(shell)
    if completion_cls is None:  # pragma: no cover - guarded by Choice
        raise click.BadParameter(f"unsupported shell '{shell}'")
    complete_var = "_BLAZE_HAMMER_COMPLETE"
    prog_name = "blaze-hammer"
    script = completion_cls(cli, {}, prog_name, complete_var).source()
    click.echo(script)


from blaze_hammer.cli.faker_cmd import faker_group  # noqa: E402
from blaze_hammer.cli.init_cmd import init  # noqa: E402
from blaze_hammer.cli.inspect_cmd import inspect, validate  # noqa: E402
from blaze_hammer.cli.interactive_cmd import interactive  # noqa: E402
from blaze_hammer.cli.placeholders_cmd import placeholders  # noqa: E402
from blaze_hammer.cli.profiles_cmd import profile, profiles  # noqa: E402
from blaze_hammer.cli.run_cmd import run  # noqa: E402
from blaze_hammer.cli.web_cmd import web  # noqa: E402

cli.add_command(run)
cli.add_command(init)
cli.add_command(web)
cli.add_command(inspect)
cli.add_command(validate)
cli.add_command(placeholders)
cli.add_command(profiles)
cli.add_command(profile)
cli.add_command(interactive)
cli.add_command(faker_group)
cli.add_command(version)
cli.add_command(completion)


def _route_legacy(argv: list[str]) -> list[str]:
    """Normalize shorthand invocations into the ``run`` command.

    All forms share one execution pipeline (no special-casing)::

        bh                     -> run            (target from blazehammer.yaml)
        bh <url> [opts]        -> run <url>      (positional overrides YAML target)
        bh <flags> [opts]      -> run <flags>    (--config, -n, --dry-run, ...)

    Exempted from routing: exact ``--version``/``-V`` and the group help
    options, plus any registered subcommand.
    """
    if not argv:
        return ["run"]
    first = argv[0]
    lowered = first.lower()
    if lowered in SUBCOMMANDS:
        return argv
    if lowered == "--web":  # 'bh --web' === 'bh web'
        return ["web", *argv[1:]]
    if first in ("-V", "--version", "-h", "--help"):
        return argv
    return ["run", *argv]


def cli_entry(argv: list[str] | None = None) -> None:
    """Process entry point (console script + python -m)."""
    args = list(sys.argv[1:] if argv is None else argv)
    args = _route_legacy(args)
    cli(args=args, prog_name=_infer_prog_name())


def _infer_prog_name() -> str | None:
    try:
        name = Path(sys.argv[0]).name or None
        if name and name.lower().endswith(".exe"):
            name = name[:-4]
        if name == "__main__.py":
            return "blaze-hammer"
        return name
    except Exception:  # pragma: no cover - defensive
        return None


def main() -> int:
    """Compatibility entry for ``python main.py`` / ``python -m blaze_hammer``."""
    try:
        cli_entry()
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else (EXIT_USAGE if code else 0)
    return 0
