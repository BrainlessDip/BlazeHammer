# AGENTS.md

Blaze Hammer: async API load-testing CLI, restructured (1.0.0) into the `blaze_hammer/` package. Python >= 3.13. Managed with **uv**.

## Layout & entry points

- `blaze_hammer/cli/` — Click app (`run`, `init`, `inspect`, `validate`, `placeholders`, `profiles`+`profile`, `interactive`, `version`, `completion`, `faker`). CLI contains no business logic; it delegates to `blaze_hammer/services.py`.
- `blaze_hammer/config/models.py` — pydantic `RunConfig`; built once per invocation by `config/loader.py` with merge priority **CLI > BLAZE_*/BLAZE_HAMMER_* env > profile > blazehammer.yaml (config/project.py) > defaults**.
- `blaze_hammer/config/project.py` — the ONLY reader of `blazehammer.yaml`: discovery is cwd-only (no parent walk), unknown-key rejection with suggestions, friendly aliases, payload/header paths resolved against the YAML's own directory.
- `blaze_hammer/templating/` — placeholder registry + resolver + pre-flight validation. `build_default_registry()` preserves legacy dispatch order (exact tokens, then ordered prefix chain).
- `blaze_hammer/engine/` — runner uses fixed workers + bounded queue; only the scheduler resolves templates (this is what makes `--seed` deterministic). No Rich imports in engine/stats.
- `blaze_hammer/output/` — console/presenter/live dashboard/exporters; ASCII-safe text (this repo is developed on a legacy cp1252 Windows console where even Rich crashes on ✓/✗ glyphs — don't add non-cp1252 chars to default output).
- `blaze_hammer/ext/` — USER-EDITABLE extension files (`parsers.py`, `providers.py`, `attachments.py`). Legacy paths `utils/custom_*.py` are import shims; `utils/replace_placeholders.py` and `utils/compare_json.py` are deprecated shims.
- `blaze_hammer/web/` — headless FastAPI API/WebSocket server (`bh web` / `bh --web`); the Python package ships NO frontend (React lives in a separate repo). Thin transport only: runs start through `services.prepare_run`/`execute_prepared` (the exact CLI pipeline; `templates=` param carries inline editor JSON, paired with `RunConfig.inline_templates` which relaxes file-presence validation). All routes under `/api/v1`, socket at `/api/v1/ws`; errors use the `{error:{code,message}}` envelope (`web/app.py` handlers). Auth is scrypt (`web/auth.py`) + server-side sessions; every route and `/ws` requires the session when `web.auth.enabled`. Web settings ride `RunConfig.web` through the normal merge pipeline (env: `BLAZE_HAMMER_WEB_*`/`BLAZE_WEB_*`/`BH_WEB_*`; CORS origins: `web.cors.origins`).
- Entry points: `blaze-hammer` console script, `python -m blaze_hammer`, and legacy `main.py` shim (bare URL / `--json-diff` argv routes to `run`). Exit codes: 0 ok, 1 config/validation, 2 usage, 130 interrupted.
- `blaze_hammer/files/templates.py` — TemplateService primitives shared by the web API (and future CLI): SHA-256 `compute_revision`, `validate_json` (syntax only — placeholders are plain strings), `atomic_write_text` (tmp→fsync→`os.replace`, trailing-newline normalization). Routes in `web/routes/config_routes.py` must call these through the module (`template_files.fn`) so tests can monkeypatch.
- `blaze_hammer/templating/catalog.py` — placeholder/Faker autocomplete catalog derived from the live registry + installed Faker (custom providers included, signature-inspected, never executed); cached per locale via `_CATALOG_CACHE` (`invalidate_catalog_cache()` after provider/locale changes). Served at `/api/v1/placeholders/catalog` and pushed as `placeholder.catalog` right after the WS `hello`.
- **Response snapshots**: `engine/client.read_body_snapshot` streams ≤`response_logging.max_body_bytes` bytes, short-circuits binary content types (connection closed unread), and decodes via declared charset→UTF-8 with a `[unable to decode…]` fallback. Storage gating lives in `web/runs.response_snapshot_fields` (mode none/errors/all; errors-mode stores only failures; binary placeholders count as bodies). Runner counts non-2xx/3xx as failed (`ok=response.is_success`) — stats/summaries/log all derive from that. Log entries live in a per-run bounded deque (200) surfaced as typed `ResponseSnapshot`s.

## Commands

```bash
uv sync --dev                      # install (project installs editable via hatchling)
uv run ruff check . && uv run ruff format --check .
uv run mypy                        # pydantic plugin configured
uv run pytest                      # or: uv run pytest --cov=blaze_hammer --cov-fail-under=85
```

No network needed for tests: integration tests spin up local threaded HTTP servers (`tests/conftest.py`).

## Gotchas

- **h2**: dependency comes from the `httpx[http2]` extra — do not drop the extra; `http2=True` is hardcoded in `engine/client.py`.
- **Legacy quirks preserved**: `run --json-diff` always exits 1 (even success); `-ph/-pp/-pr`, `-jd`, `-dh`, `-fp`, `-pt`, `-s` short flags kept; bare-URL invocation without subcommand auto-routes to `run` (`cli/main.py:_route_legacy`).
- **Shorthand routing** (`cli/main.py:_route_legacy`): empty argv, bare URLs, and flag-first argv all normalize to `run`; only exact `-V/--version/-h/--help` and registered subcommands stay at group level. Tests must invoke through `_route_legacy` (see `tests/integration/test_init.py:_invoke`) or routing is bypassed.
- **Project init safety**: project names are validated as raw strings BEFORE path construction (`services.validate_project_name`) — pathlib silently normalizes `.`/`..`/separators away, so validating `Path(x).name` is a traversal hole.
- Placeholder values are **strings only when embedded**; a placeholder occupying the entire value returns its native JSON type for specs flagged `native=True` in `templating/builtins.py` (`{int}`→`42`, `{bool}`→`true`, `{null}`, `{list}`→array). Legacy string behavior is preserved for all pre-1.1 keywords not flagged native.
- Unknown `{tokens}` stay verbatim at runtime but fail pre-flight validation — tests assert both behaviors.
- Argument validation lives in per-spec `validator=` callables (registry metadata), NOT in `validation.py` if-chains; add validators when adding placeholders with args, and keep coercion identical between validator and handler.
- `payload_example.json` must keep passing `blaze-hammer validate -p payload_example.json` (it's the docs example).
- README documents every flag/placeholder — update it whenever argparse/click options or placeholder syntax change.
- **Faker integration**: `{faker.*}` tokens are resolved dynamically against the installed Faker instance (not hardcoded). `resolve_faker_token` returns raw native values; the resolver coerces to text only when embedded. `FakerFactory` accepts `default_locale` from `--faker-locale`. Security guardrails block dunder attrs and dangerous modules (`os`, `subprocess`, `eval`). `faker.VERSION` (not `__version__`) is the version attribute.
