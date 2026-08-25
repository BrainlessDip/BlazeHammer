"""Application services: orchestration between CLI, engine and output.

The CLI layer stays free of business logic; every command delegates here.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.columns import Columns
from rich.prompt import Confirm, Prompt
from rich.table import Table

from blaze_hammer.config import loader as config_loader
from blaze_hammer.config.logging_setup import setup_logging
from blaze_hammer.config.models import RunConfig
from blaze_hammer.config.validation import (
    ensure_config_valid,
    summarize_placeholders,
    validate_config,
)
from blaze_hammer.engine.client import build_client
from blaze_hammer.engine.planner import RequestPlanner, RequestTemplates
from blaze_hammer.engine.rate_limiter import TokenBucket
from blaze_hammer.engine.runner import LoadTestRunner, RequestOutcome
from blaze_hammer.errors import (
    EXIT_ERROR,
    EXIT_INTERRUPTED,
    EXIT_OK,
    BlazeHammerError,
    ConfigurationError,
)
from blaze_hammer.files.attachments import AttachmentSet, load_attachment_spec
from blaze_hammer.output.console import make_console, plain_error, render_error
from blaze_hammer.output.exporters import ResponseRecorder, export_results, write_summary
from blaze_hammer.output.filters import ResponseFilter, truncate_text
from blaze_hammer.output.json_diff import diff_tree, render_diff
from blaze_hammer.output.live import live_dashboard, null_dashboard
from blaze_hammer.output.presenter import Presenter, banner
from blaze_hammer.output.preview import render_request_plans
from blaze_hammer.parsers import render_headers, render_payload, render_response
from blaze_hammer.security.redaction import redact_mapping
from blaze_hammer.templating import (
    FakerFactory,
    ResolveContext,
    TemplateResolver,
    TemplateValidator,
    build_default_registry,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import Any

    from blaze_hammer.stats.collector import StatsCollector
    from blaze_hammer.stats.models import RunStats

LARGE_RUN_THRESHOLD = 10_000


# ------------------------------------------------------------ construction --


def build_run_config(
    overrides: dict,
    profile: str | None,
    config_path: str | None = None,
) -> RunConfig:
    """CLI -> env -> profile -> YAML -> defaults merge + schema validation."""
    return config_loader.build_config(overrides, profile=profile, config_path=config_path)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"Cannot read {path}", reason=str(exc)) from exc


def _load_templates(cfg: RunConfig) -> tuple[dict | None, dict | None, dict[str, str]]:
    """Return (headers_dict, payload_dict, {label: raw_text})."""
    raw_texts: dict[str, str] = {}
    headers = payload = None
    if cfg.headers_file is not None and not cfg.disable_headers:
        path = Path(cfg.headers_file)
        raw_texts[str(path)] = _read_text(path)
        headers = config_loader.load_json_file(path, "headers.json")
    if cfg.payload_file is not None:
        path = Path(cfg.payload_file)
        raw_texts[str(path)] = _read_text(path)
        payload = config_loader.load_json_file(path, "payload.json")
    return headers, payload, raw_texts


def _build_resolver(seed: int | None, faker_locale: str | None = None) -> TemplateResolver:
    registry = build_default_registry()
    rng = random.Random(seed)
    ctx = ResolveContext(
        rng=rng,
        faker=FakerFactory(rng, default_locale=faker_locale),
        seeded=seed is not None,
    )
    return TemplateResolver(registry, ctx)


def _template_pairs(
    cfg: RunConfig,
    headers: dict | None,
    payload: dict | None,
    raw_texts: dict[str, str],
) -> dict[str, tuple[Any, str]]:
    """Map label -> (parsed_object, raw_text) for the validator."""
    pairs: dict[str, tuple[Any, str]] = {}
    if headers is not None and cfg.headers_file is not None:
        key = str(cfg.headers_file)
        pairs[key] = (headers, raw_texts.get(key, ""))
    if payload is not None and cfg.payload_file is not None:
        key = str(cfg.payload_file)
        pairs[key] = (payload, raw_texts.get(key, ""))
    return pairs


def _validate_placeholders(cfg: RunConfig) -> None:
    headers, payload, raw_texts = _load_templates(cfg)
    validator = TemplateValidator(build_default_registry(), faker_locale=cfg.faker_locale)
    report = validator.validate(_template_pairs(cfg, headers, payload, raw_texts))
    if not report.ok:
        raise ConfigurationError(
            "Invalid placeholder(s) found",
            reason=report.render(),
            hint="fix the tokens above before running",
        )


def _handle_error(presenter: Presenter, cfg: RunConfig, error: BlazeHammerError) -> int:
    if presenter.simple:
        presenter.console.print(plain_error(error))
    else:
        render_error(presenter.console, error, debug=cfg.output.debug)
    return error.exit_code


# -------------------------------------------------------------------- run --


def load_run_templates(cfg: RunConfig) -> tuple[dict | None, dict | None]:
    """Public alias: load headers/payload templates for a run."""
    headers, payload, _raw = _load_templates(cfg)
    return headers, payload


def build_resolver(seed: int | None, faker_locale: str | None = None) -> TemplateResolver:
    """Public alias: build the per-run template resolver."""
    return _build_resolver(seed, faker_locale)


def validate_placeholders(cfg: RunConfig) -> None:
    """Public alias: pre-flight placeholder validation."""
    _validate_placeholders(cfg)


@dataclass
class PreparedRun:
    """Everything needed to execute (or preview) one load test.

    Built by :func:`prepare_run`; shared by the CLI runner and the Web GUI
    so both drive the identical request pipeline.
    """

    cfg: RunConfig
    client: Any  # httpx.AsyncClient
    planner: RequestPlanner
    collector: StatsCollector | None = None
    runner: LoadTestRunner | None = None
    recorder: ResponseRecorder | None = None
    attachments: AttachmentSet | None = None

    def close_resources(self) -> None:
        """Close recorder + attachments (idempotent; never raises)."""
        if self.recorder is not None:
            self.recorder.close()
            self.recorder = None
        if self.attachments is not None:
            self.attachments.close()
            self.attachments = None

    async def aclose(self) -> None:
        """Close the HTTP client plus all resources."""
        try:
            await self.client.aclose()
        finally:
            self.close_resources()


def prepare_run(
    cfg: RunConfig,
    *,
    observers: Sequence[Callable[[RequestOutcome], None]] = (),
    observer_factory: Callable[[RequestPlanner], Sequence[Callable[[RequestOutcome], None]]]
    | None = None,
    with_runner: bool = True,
    templates: RequestTemplates | None = None,
) -> PreparedRun:
    """Validate config and assemble client/planner/collector/runner.

    ``with_runner=False`` stops before collector/observers for preview-only
    use. External *observers* run first, then factory-built ones (CLI
    printer), then the response recorder — matching historical ordering.
    *templates* overrides payload/header files (Web GUI inline editors).
    """
    from blaze_hammer.stats.collector import StatsCollector

    ensure_config_valid(cfg)
    _validate_placeholders(cfg)

    attachments: AttachmentSet | None = None
    if cfg.file_payload:
        spec = load_attachment_spec()
        if not spec:
            raise ConfigurationError(
                "--file-payload is set but no attachments are configured",
                hint="add files to blaze_hammer/ext/attachments.py",
            )
        attachments = AttachmentSet(spec)
        attachments.open()

    try:
        client = build_client(cfg)
        if templates is None:
            headers_t, payload_t = load_run_templates(cfg)
            templates = RequestTemplates(headers=headers_t, payload=payload_t)
        planner = RequestPlanner(
            cfg,
            build_resolver(cfg.seed, cfg.faker_locale),
            templates,
            attachments,
        )
    except Exception:
        if attachments is not None:
            attachments.close()
        raise

    prepared = PreparedRun(cfg=cfg, client=client, planner=planner, attachments=attachments)
    if not with_runner:
        return prepared

    collector = StatsCollector()
    recorder = _make_recorder(cfg, planner)
    chain: list[Callable[[RequestOutcome], None]] = list(observers)
    if observer_factory is not None:
        chain.extend(observer_factory(planner))
    if recorder is not None:
        chain.append(recorder)

    runner = LoadTestRunner(
        cfg,
        planner,
        client,
        collector,
        bucket=TokenBucket(cfg.rate) if cfg.rate else None,
        observers=chain,
    )
    prepared.collector = collector
    prepared.runner = runner
    prepared.recorder = recorder
    return prepared


async def execute_prepared(prepared: PreparedRun) -> RunStats:
    """Drive a prepared runner to completion and close the client."""
    assert prepared.runner is not None and prepared.collector is not None
    try:
        return await prepared.runner.run()
    finally:
        await prepared.client.aclose()


def run_load_test(cfg: RunConfig) -> int:
    """Execute a full run; returns the process exit code."""
    setup_logging(level=cfg.output.log_level, log_file=cfg.output.log_file)
    presenter = Presenter(cfg)
    prepared: PreparedRun | None = None
    try:
        if cfg.preview.dry_run or cfg.preview.preview_count:
            prepared = prepare_run(cfg, with_runner=False)
            count = cfg.preview.preview_count or 1
            plans = prepared.planner.preview_plans(count)
            banner(presenter.console, cfg)
            render_request_plans(
                presenter.console,
                plans,
                sensitive_names=prepared.planner.sensitive_names,
                max_payload_chars=cfg.output.max_response_size,
                dry_run=cfg.preview.dry_run,
            )
            return EXIT_OK

        def _factory(
            planner: RequestPlanner,
        ) -> list[Callable[[RequestOutcome], None]]:
            if cfg.prints_per_request:
                return [_make_printer(cfg, presenter, planner)]
            return []

        prepared = prepare_run(cfg, observer_factory=_factory)
        assert prepared.collector is not None
        collector = prepared.collector

        if not _confirm_large_run(cfg, presenter):
            presenter.console.print("[yellow]Aborted by user.[/yellow]")
            return EXIT_ERROR

        banner(presenter.console, cfg)
        dashboard = (
            null_dashboard()
            if presenter.simple
            else live_dashboard(presenter.console, collector, cfg.requests)
        )
        with dashboard:
            try:
                stats = asyncio.run(execute_prepared(prepared))
            except KeyboardInterrupt:
                stats = collector.snapshot(
                    target=cfg.target,
                    method=cfg.method.value,
                    requested=cfg.requests,
                    interrupted=True,
                )
                with suppress(Exception):
                    asyncio.run(prepared.client.aclose())

        presenter.final_summary(stats)
        _write_exports(cfg, stats)
        return EXIT_INTERRUPTED if stats.interrupted else EXIT_OK
    except BlazeHammerError as error:
        return _handle_error(presenter, cfg, error)
    except Exception as error:  # noqa: BLE001 - concise errors unless --debug
        if cfg.output.debug:
            raise
        wrapped = BlazeHammerError(
            f"Unexpected error: {error}",
            hint="run with --debug for a traceback",
        )
        return _handle_error(presenter, cfg, wrapped)
    finally:
        if prepared is not None:
            prepared.close_resources()


class StatsCollectorSlot:
    """Tiny holder so both the runner and interrupt fallback share state."""

    def __init__(self) -> None:
        from blaze_hammer.stats.collector import StatsCollector

        self.collector = StatsCollector()


def _make_recorder(cfg: RunConfig, planner: RequestPlanner) -> ResponseRecorder | None:
    if cfg.output.save_responses_dir is None:
        return None
    return ResponseRecorder(
        cfg.output.save_responses_dir,
        sensitive_names=planner.sensitive_names,
        max_body_chars=cfg.output.max_response_size,
    )


def _write_exports(cfg: RunConfig, stats: RunStats) -> None:
    if cfg.output.save_responses_dir is not None:
        write_summary(cfg.output.save_responses_dir, stats)
    if cfg.output.export_path is not None:
        export_results(cfg.output.export_path, stats)


def _make_printer(
    cfg: RunConfig,
    presenter: Presenter,
    planner: RequestPlanner,
) -> Callable[[RequestOutcome], None]:
    response_filter = ResponseFilter.from_output(cfg.output)

    def observe(outcome: RequestOutcome) -> None:
        if not response_filter.matches(outcome):
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        status = outcome.status_code if outcome.status_code is not None else "-"
        c = presenter.console

        def emit(label: str, content: str | None, style: str) -> None:
            if content is None:
                return
            c.print(f"[dim]{timestamp}[/dim] [{style}]{label}[/{style}] {status}\n{content}\n")

        if cfg.output.print_headers:
            safe = redact_mapping(outcome.resolved_headers or {}, planner.sensitive_names)
            emit("Headers", render_headers(outcome.status_code, safe), "magenta")
        if cfg.output.print_payload:
            safe = redact_mapping(outcome.resolved_payload or {}, planner.sensitive_names)
            emit("Payload", render_payload(outcome.status_code, safe), "yellow")
        if cfg.output.print_response:
            text = outcome.body.text if outcome.body else None
            shown, omitted = truncate_text(text, cfg.output.max_response_size)
            content = render_response(outcome.status_code, shown)
            if omitted and content is not None:
                content = f"{content}\n... {omitted:,} characters truncated"
            emit("Response", content, "yellow")

    return observe


def _confirm_large_run(cfg: RunConfig, presenter: Presenter) -> bool:
    if cfg.requests < LARGE_RUN_THRESHOLD or cfg.assume_yes:
        return True
    if not sys.stdout.isatty():
        return True
    presenter.console.print(
        f"[bold yellow]About to send {cfg.requests:,} requests to {cfg.target}[/bold yellow]"
    )
    return Confirm.ask("Continue?", default=True)


# ---------------------------------------------------------------- inspect --


def inspect_config(cfg: RunConfig, *, show_sample: bool) -> int:
    """Validate and explain the configuration; optionally show one request."""
    setup_logging(level=cfg.output.log_level, log_file=cfg.output.log_file)
    presenter = Presenter(cfg)
    console = presenter.console
    try:
        outcome = validate_config(cfg)
        headers, payload, raw_texts = _load_templates(cfg)

        validator = TemplateValidator(build_default_registry(), faker_locale=cfg.faker_locale)
        placeholder_report = validator.validate(_template_pairs(cfg, headers, payload, raw_texts))

        console.print("[bold]Configuration[/bold]")
        console.print("-" * 40)
        from blaze_hammer.config.project import find_project_config

        project_file = find_project_config()
        grid_rows = [
            ("Target", cfg.target),
            ("Method", cfg.method.value),
            ("Requests", f"{cfg.requests:,}"),
            ("Concurrency", str(cfg.concurrency)),
            ("Delay", f"{cfg.delay}s" if cfg.delay else "-"),
            ("Rate limit", f"{cfg.rate}/s" if cfg.rate else "-"),
            ("Timeout", f"{cfg.timeout}s"),
            ("Retries", str(cfg.retries.max_retries)),
            ("Seed", str(cfg.seed) if cfg.seed is not None else "-"),
            ("Faker locale", cfg.faker_locale or "default"),
            ("Profile", cfg.profile or "-"),
            (
                "Project",
                str(project_file) if project_file is not None else "(no blazehammer.yaml)",
            ),
        ]
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold")
        grid.add_column()
        for label, value in grid_rows:
            grid.add_row(label, str(value))
        if payload is not None:
            grid.add_row("Payload file", str(cfg.payload_file))
            grid.add_row("Body type", cfg.post_type.value)
            grid.add_row("Fields", str(_count_leaves(payload)))
        if headers is not None:
            grid.add_row("Headers file", str(cfg.headers_file))
        console.print(grid)

        faker_count = builtin_count = env_count = 0
        for text in raw_texts.values():
            summary = summarize_placeholders(text)
            faker_count += summary["faker"]
            builtin_count += summary["builtin"]
            env_count += summary["env"]
        console.print("\n[bold]Placeholders[/bold]")
        console.print(f"  Faker      {faker_count}")
        console.print(f"  Built-in   {builtin_count}")
        console.print(f"  Env refs   {env_count}")

        console.print("\n[bold]Validation[/bold]")
        for label, ok, detail in outcome.checks:
            mark = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
            suffix = f" - {detail}" if detail else ""
            console.print(f"  {mark} {label}{suffix}")
        if placeholder_report.ok:
            console.print("  [green]OK[/green] Placeholders valid")
        else:
            console.print()
            console.print(placeholder_report.render())

        failures = list(outcome.failures)
        if not placeholder_report.ok:
            failures.append("placeholders invalid")
        if failures:
            return EXIT_ERROR

        if show_sample:
            resolver = _build_resolver(cfg.seed, cfg.faker_locale)
            planner = RequestPlanner(
                cfg, resolver, RequestTemplates(headers=headers, payload=payload)
            )
            plan = planner.next_plan(0)
            console.print("\n[bold]Generated request[/bold]")
            console.print("-" * 40)
            render_request_plans(
                console,
                [plan],
                sensitive_names=planner.sensitive_names,
                max_payload_chars=cfg.output.max_response_size,
                dry_run=False,
            )
        return EXIT_OK
    except BlazeHammerError as error:
        return _handle_error(presenter, cfg, error)


def _count_leaves(node: object) -> int:
    if isinstance(node, dict):
        return sum(_count_leaves(value) for value in node.values())
    if isinstance(node, list):
        return sum(_count_leaves(item) for item in node)
    return 1


# ----------------------------------------------------------- placeholders --


def placeholders_docs(query: str | None) -> int:
    console = make_console()
    registry = build_default_registry()
    if query is None or query == "all":
        table = Table(title="Built-in placeholders")
        table.add_column("Syntax", style="cyan", no_wrap=True)
        table.add_column("Returns", style="magenta")
        table.add_column("Description")
        for spec in registry.specs():
            syntax = spec.syntax or f"{{{spec.keyword}}}"
            table.add_row(syntax, spec.returns, _first_line(spec.description))
        console.print(table)
        console.print(
            "\n[dim]Faker providers: {faker.name}, {faker.providers.internet.email}, "
            "{faker.custom(field=job, locale=en_US)}, {faker.profile(field=job)}\n"
            "Details: 'blaze-hammer placeholders <name>' (e.g. placeholders otp)\n"
            "Browse faker: 'blaze-hammer placeholders faker [query]'[/dim]"
        )
        return EXIT_OK

    if query.startswith("faker"):
        subquery = query[len("faker") :].strip().lstrip(".").lower()
        rng = random.Random(0)
        candidates = FakerFactory(rng).attribute_candidates()
        matches = [name for name in candidates if not subquery or subquery in name.lower()]
        if query == "faker":
            console.print(f"[bold]{len(matches)} faker methods[/bold] (showing first 80)")
        console.print(Columns(matches[:80]))
        if len(matches) > 80:
            console.print(f"[dim]... and {len(matches) - 80} more; refine the query.[/dim]")
        return EXIT_OK

    probe_query = query.lstrip("{").rstrip("}")
    hit = registry.match(probe_query)
    if hit is None:
        console.print(f"No placeholder matches '{query}'.")
        return EXIT_ERROR
    spec, _handler = hit
    console.print(f"[bold cyan]{spec.syntax or '{' + spec.keyword + '}'}[/bold cyan]\n")
    console.print(spec.description)
    if spec.arguments:
        console.print("\n[bold]Arguments:[/bold]")
        for line in spec.arguments.splitlines():
            console.print(f"  {line}")
    if spec.default:
        console.print(f"\n[bold]Defaults:[/bold] {spec.default}")
    console.print(f"\n[bold]Returns:[/bold] {spec.returns}")
    if spec.example:
        console.print(f"\n[bold]Example:[/bold] {spec.example}")
    return EXIT_OK


def _first_line(text: str) -> str:
    return text.splitlines()[0] if text else ""


# ----------------------------------------------------------------- faker --


def faker_list_providers(*, query: str = "", locale: str | None = None) -> int:
    """List Faker providers/methods, grouped by provider family."""
    import importlib

    console = make_console()
    rng = random.Random(0)
    factory = FakerFactory(rng, default_locale=locale)
    families = factory.list_providers(locale)

    query_lower = query.lower()

    # Filter by query if provided
    if query_lower:
        families = {
            family: [m for m in methods if query_lower in m.lower()]
            for family, methods in families.items()
            if query_lower in family.lower() or any(query_lower in m.lower() for m in methods)
        }
        families = {k: v for k, v in families.items() if v}

    if not families:
        console.print(f"[yellow]No Faker methods match '{query}'.[/yellow]")
        faker_version = importlib.import_module("faker").VERSION
        console.print(f"[dim]Installed Faker version: {faker_version}[/dim]")
        return EXIT_OK

    faker_version = importlib.import_module("faker").VERSION
    total = sum(len(m) for m in families.values())
    console.print(f"[bold]{total} Faker methods[/bold] [dim](Faker {faker_version})[/dim]\n")

    for family in sorted(families):
        methods = families[family]
        console.print(f"  [bold cyan]{family}[/bold cyan]")
        for method in sorted(methods):
            console.print(f"    {method}")
        console.print()

    console.print(
        "[dim]Browse: 'blaze-hammer faker show <method>'[/dim]\n"
        "[dim]Use in payloads: {faker.name}, {faker.random_int(min=1,max=100)}[/dim]"
    )
    return EXIT_OK


def faker_show_method(*, method: str, locale: str | None = None) -> int:
    """Show details for a specific Faker method."""
    import importlib

    from rich.table import Table

    console = make_console()
    rng = random.Random(0)
    factory = FakerFactory(rng, default_locale=locale)

    # Strip leading "faker." if provided
    clean = method.removeprefix("faker.")
    info = factory.get_method_info(clean, locale)

    if info is None:
        fake = factory.get(locale)
        if not hasattr(fake, clean):
            # Try provider path
            candidates = factory.attribute_candidates(locale) + factory.provider_path_candidates(
                locale
            )
            from blaze_hammer.templating.faker_bridge import suggest_faker_fields

            suggestions = suggest_faker_fields(clean, candidates)
            console.print(f"[bold red]Faker method '{clean}' not found.[/bold red]")
            if suggestions:
                console.print("\n[bold]Did you mean:[/bold]")
                for s in suggestions:
                    console.print(f"  faker.{s}")
            faker_version = importlib.import_module("faker").VERSION
            console.print(f"\n[dim]Installed Faker version: {faker_version}[/dim]")
            return EXIT_ERROR

        # Attribute exists but can't be introspected
        console.print(f"[bold cyan]faker.{clean}[/bold cyan]\n")
        attr = getattr(fake, clean)
        if callable(attr):
            console.print("[bold]Type:[/bold] method (callable)")
            doc = (attr.__doc__ or "").strip()
            if doc:
                console.print(f"\n[bold]Description:[/bold]\n  {doc}")
        else:
            console.print(f"[bold]Type:[/bold] property ({type(attr).__name__})")
            console.print(f"[bold]Value:[/bold] {attr}")
        console.print(f"\n[bold]Example:[/bold] {{faker.{clean}}}")
        return EXIT_OK

    # Full info display
    console.print(f"[bold cyan]faker.{clean}[/bold cyan]\n")

    if info.get("doc"):
        console.print(f"[bold]Description:[/bold]\n  {info['doc']}\n")

    params = info.get("params", [])
    if params:
        table = Table(title="Arguments", show_header=True, padding=(0, 1))
        table.add_column("Name", style="cyan")
        table.add_column("Kind")
        table.add_column("Default")
        table.add_column("Type")
        for p in params:
            table.add_row(
                p["name"],
                p.get("kind", "").replace("_", " ").title(),
                p.get("default", "-"),
                p.get("annotation", "-"),
            )
        console.print(table)
    else:
        console.print("[bold]Arguments:[/bold] none (or *args/**kwargs)")

    # Try a sample call
    fake = factory.get(locale)
    attr = getattr(fake, clean, None)
    if callable(attr):
        try:
            import inspect as _inspect

            sig = _inspect.signature(attr)
            # Only try if no required args
            required = [
                p
                for p in sig.parameters.values()
                if p.default is _inspect.Parameter.empty
                and p.name not in ("self", "cls")
                and p.kind
                in (
                    _inspect.Parameter.POSITIONAL_ONLY,
                    _inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            if not required:
                sample = attr()
                console.print(f"\n[bold]Sample output:[/bold] {sample}")
        except Exception:
            pass

    faker_version = importlib.import_module("faker").VERSION
    console.print(f"\n[dim]Installed Faker version: {faker_version}[/dim]")
    console.print(f"\n[bold]Example:[/bold] {{faker.{clean}}}")
    return EXIT_OK


# ---------------------------------------------------------------- profiles --


def profiles_list() -> int:
    console = make_console()
    directory = config_loader.PROFILES_DIR
    if not directory.is_dir():
        console.print("[yellow]No profiles/ directory found.[/yellow]")
        console.print(f"Create JSON files in ./{directory}/ to define reusable test configs.")
        return EXIT_OK
    profiles = sorted(directory.glob("*.json"))
    if not profiles:
        console.print(f"[yellow]No profiles found in ./{directory}/[/yellow]")
        return EXIT_OK
    from rich.table import Table

    table = Table(title="Profiles")
    table.add_column("Name", style="cyan")
    table.add_column("Path")
    for profile in profiles:
        table.add_row(profile.stem, str(profile))
    console.print(table)
    return EXIT_OK


def profiles_show(name: str) -> int:
    console = make_console()
    profile_data = config_loader.load_profile(name)
    console.print(f"[bold]Profile '{name}'[/bold]")
    console.print("-" * 40)
    console.print_json(json.dumps(profile_data))
    console.print(
        "\n[dim]Priority when running: CLI arguments > environment variables > "
        "this profile > blazehammer.yaml > defaults.[/dim]"
    )
    return EXIT_OK


# -------------------------------------------------------------------- init --


def validate_project_name(name: str) -> str:
    """Reject empty, path-like or traversal project names."""
    import re

    cleaned = name.strip()
    if not cleaned:
        raise ConfigurationError(
            "Invalid project name",
            reason="project name is empty",
            hint="use a filesystem-safe name like 'my-api-test'",
        )
    if cleaned in (".", ".."):
        raise ConfigurationError(
            "Invalid project name",
            reason=f"'{cleaned}' is not a valid directory name",
        )
    if re.search(r"[\\/]", cleaned):
        raise ConfigurationError(
            "Invalid project name",
            reason=f"'{cleaned}' contains a path separator (possible traversal)",
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", cleaned):
        raise ConfigurationError(
            "Invalid project name",
            reason=f"'{cleaned}' contains characters outside [A-Za-z0-9._-]",
            hint="use a filesystem-safe name like 'my-api-test' or 'blaze-test-01'",
        )
    return cleaned


def init_project(  # noqa: PLR0913
    *,
    root: Path,
    target: str,
    method: str,
    requests: int,
    concurrency: int,
    create_examples: bool = True,
    force: bool = False,
) -> list[str]:
    """Create the project tree; returns names of files/dirs written.

    Raises ConfigurationError when *root* exists and *force* is false.
    ``.gitignore`` / ``.env.example`` are only written when absent.
    """
    from blaze_hammer.config.project import (
        TEMPLATE_HEADERS,
        TEMPLATE_PAYLOAD,
        load_project_yaml,
        render_template_yaml,
    )

    name = validate_project_name(root.name)
    root = root.with_name(name)
    if root.exists() and not force:
        raise ConfigurationError(
            f"Directory already exists: {root}",
            reason="refusing to overwrite an existing project",
            hint="choose another project name, or use --force to overwrite",
        )

    created: list[str] = []
    root.mkdir(parents=True, exist_ok=True)
    created.append("")

    yaml_path = root / "blazehammer.yaml"
    yaml_path.write_text(
        render_template_yaml(
            target=target, method=method, requests=requests, concurrency=concurrency
        ),
        encoding="utf-8",
    )
    # Guarantee the generated file parses through the real loader.
    load_project_yaml(yaml_path)
    created.append("blazehammer.yaml")

    profiles_dir = root / "profiles"
    if not profiles_dir.exists():
        profiles_dir.mkdir()
        created.append("profiles/")

    examples = (("payload.json", TEMPLATE_PAYLOAD), ("headers.json", TEMPLATE_HEADERS))
    if create_examples:
        for filename, template in examples:
            path = root / filename
            path.write_text(template, encoding="utf-8")
            created.append(filename)

    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(".env\nresults/\n__pycache__/\n", encoding="utf-8")
        created.append(".gitignore")

    env_example = root / ".env.example"
    if not env_example.exists():
        env_example.write_text(
            "# Blaze Hammer environment overrides (all optional)\n"
            "# BLAZE_HAMMER_TARGET=https://api.example.com\n"
            "# BLAZE_HAMMER_REQUESTS=1000\n"
            "# BLAZE_HAMMER_CONCURRENCY=50\n"
            "# BLAZE_HAMMER_TIMEOUT=30\n"
            "# BLAZE_HAMMER_FAKER_LOCALE=bn_BD\n",
            encoding="utf-8",
        )
        created.append(".env.example")
    _ = name
    return created


def profiles_create(name: str) -> int:
    """Write a minimal override-only profile template into ./profiles/."""
    console = make_console()
    safe = validate_project_name(name)  # same filesystem-safety rules
    directory = config_loader.PROFILES_DIR
    directory.mkdir(exist_ok=True)
    path = directory / f"{safe}.json"
    if path.exists():
        raise ConfigurationError(
            f"Profile already exists: {path}",
            hint="choose another profile name or edit the file directly",
        )
    template = {
        "target": "https://api.example.com/register",
        "method": "POST",
        "payload": "payload.json",
    }
    path.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    console.print(f"[green]Created[/green] {path}")
    console.print("\n[dim]Run with: blaze-hammer run --profile " + safe + "[/dim]")
    console.print("[dim]Profiles only need values that differ from blazehammer.yaml.[/dim]")
    return EXIT_OK


# -------------------------------------------------------------------- web --


def run_web_server(cfg: RunConfig, *, open_browser: bool = False, yes_i_know: bool = False) -> int:
    """Start the Web GUI (blocking); returns a process exit code."""
    import socket
    import threading
    import webbrowser
    from pathlib import Path as _Path

    from blaze_hammer.config.project import find_project_config
    from blaze_hammer.web.app import create_app
    from blaze_hammer.web.config import resolve_web_settings

    settings = resolve_web_settings(cfg)
    if not settings.enabled:
        raise ConfigurationError(
            "Web GUI is disabled",
            reason="web.enabled is false in blazehammer.yaml",
            hint="enable it or start the server with explicit settings",
        )

    if settings.auth.enabled and not settings.auth_ready:
        raise ConfigurationError(
            "Web authentication is enabled but credentials are not configured",
            reason="web.auth needs 'username' plus 'password' or 'password_hash'",
            hint=(
                "set them in blazehammer.yaml (or via "
                "BLAZE_HAMMER_WEB_USERNAME / BLAZE_HAMMER_WEB_PASSWORD), "
                "or disable auth with web.auth.enabled: false"
            ),
        )
    _ = yes_i_know  # only meaningful for the unauthenticated exposure check

    if not settings.auth.enabled and settings.host in ("0.0.0.0", "::"):
        raise ConfigurationError(
            "Refusing to expose the control panel on all interfaces without authentication",
            reason=f"host={settings.host} with web.auth.enabled=false",
            hint="re-run with --yes-i-know if this machine is isolated",
        )

    project_file = find_project_config()

    # Resolve port 0 up front so the printed URL is real.
    port = settings.port
    if port == 0:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind((settings.host, 0))
            port = probe.getsockname()[1]
        except OSError:
            port = 0  # let uvicorn pick; banner will show :0
        finally:
            probe.close()
        settings = type(settings)(
            enabled=settings.enabled,
            host=settings.host,
            port=port,
            auth=settings.auth,
            cors_enabled=settings.cors_enabled,
            cors_origins=settings.cors_origins,
        )

    app = create_app(
        settings=settings,
        project_dir=_Path.cwd(),
        project_file=project_file,
    )

    console = make_console()
    console.print("[bold]Blaze Hammer Web[/bold]\n")
    console.print(f"  Project:\n    {project_file if project_file else '(no blazehammer.yaml)'}")
    console.print(f"  Server:\n    http://{settings.host}:{port}")
    auth_line = "enabled" if settings.auth.enabled else "DISABLED"
    style = "green" if settings.auth.enabled else "yellow"
    console.print(f"  Authentication:\n    [{style}]{auth_line}[/{style}]")
    console.print("\n[dim]Press Ctrl+C to stop.[/dim]")

    if open_browser:

        def _open() -> None:
            display_host = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host
            webbrowser.open(f"http://{display_host}:{port}")

        threading.Timer(1.0, _open).start()

    try:
        import uvicorn

        uvicorn.run(app, host=settings.host, port=port, log_level="warning")
    except OSError as exc:
        raise ConfigurationError(
            f"Unable to start Web GUI on {settings.host}:{port}",
            reason=str(exc),
            hint="the port may already be in use; try --port 0 to auto-assign",
        ) from exc
    return EXIT_OK


# ------------------------------------------------------------- interactive --


def interactive_flow() -> int:
    console = make_console()
    console.print("[bold magenta]Blaze Hammer - interactive mode[/bold magenta]\n")

    def ask_int(label: str, default: int) -> int:
        while True:
            raw = Prompt.ask(label, default=str(default))
            try:
                return int(raw)
            except ValueError:
                console.print("[red]Please enter a whole number.[/red]")

    url = ""
    while not url:
        url = Prompt.ask("Target URL").strip()
    method = Prompt.ask("Method", choices=["GET", "POST"], default="GET").upper()
    requests = ask_int("Requests", 100)
    concurrency = max(1, ask_int("Concurrency", 10))

    overrides: dict = {
        "target": url,
        "method": method,
        "requests": requests,
        "concurrency": concurrency,
        "assume_yes": True,
    }
    payload_path = Prompt.ask("Payload file (blank to skip)", default="")
    if payload_path:
        overrides["payload_file"] = Path(payload_path)
    headers_path = Prompt.ask("Headers file (blank to skip)", default="headers.json")
    if headers_path:
        overrides["headers_file"] = Path(headers_path)
    seed_raw = Prompt.ask("Seed (blank for random)", default="")
    if seed_raw:
        try:
            overrides["seed"] = int(seed_raw)
        except ValueError:
            console.print("[yellow]Ignoring non-integer seed.[/yellow]")

    try:
        cfg = build_run_config(overrides, profile=None)
        if Confirm.ask("Preview generated request?", default=True):
            cfg = cfg.model_copy(update={"preview": {"dry_run": False, "preview_count": 1}})
    except BlazeHammerError as error:
        render_error(console, error)
        return error.exit_code
    return run_load_test(cfg)


# --------------------------------------------------------------- json diff --


def json_diff_files(files: Sequence[str]) -> int:
    """Legacy offline mode: resolve placeholders and diff each file.

    Preserves the historical exit code 1 after printing (documented).
    """
    console = make_console()
    resolver = _build_resolver(None)
    had_error = False
    for name in files:
        try:
            raw = _read_text(Path(name))
            before = json.loads(raw)
            if not isinstance(before, dict):
                raise ConfigurationError(f"{name} must contain a top-level JSON object")
            resolved = resolver.resolve_obj(before)
            entries = diff_tree(before, resolved)
            title = f"Payload differences - {name}"
            if entries:
                render_diff(console, title, entries)
            else:
                console.print(f"{title}: no differences")
        except (BlazeHammerError, json.JSONDecodeError) as exc:
            message = (
                exc.message
                if isinstance(exc, BlazeHammerError)
                else f"invalid JSON at line {exc.lineno}, column {exc.colno}"
            )
            console.print(f"[bold red]Failed to load {name}:[/] {message}")
            had_error = True
    # Legacy behavior (documented in AGENTS.md): json-diff always exits 1.
    _ = had_error
    return EXIT_ERROR
