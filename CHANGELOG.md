# Changelog

## 1.4.0 — Web GUI (`bh web`)

A production-grade local control panel built as a transport layer over the
same core the CLI drives — no second request engine.

### Added

- **`bh web` / `bh --web`** (+ `blaze-hammer` spellings): FastAPI + uvicorn
  server bound to `127.0.0.1:8080` by default, configurable through
  `web:` YAML section, environment variables (`BLAZE_HAMMER_WEB_*`,
  `BLAZE_WEB_*`, `BH_WEB_*`) and CLI flags.
- **Server-side authentication**: username/password login with scrypt-hashed
  credentials (plaintext config values are hashed in memory at startup,
  never logged or written back), HTTP-only SameSite=Strict session cookies,
  idle+absolute expiry, per-IP login rate limiting, CSRF header guard on
  mutating routes, auth-gated `/docs`. WebSocket `/ws` requires the session
  cookie; expired sessions receive `auth.expired` then close `4402`.
- **REST API**: health/me/auth/config(+save)/profiles/runs(start, stop,
  list, get, log)/preview — typed pydantic models, security headers,
  request-size cap (8 MiB).
- **Real-time dashboard**: stable JSON event protocol (`run.started`,
  `stats.updated` ~4 Hz, `request.completed`, `run.completed|stopped|error`);
  full event stream with queue-depth aggregation; reconnecting WS client
  with seq-based dedupe.
- **Frontend**: single dark-theme page (Tailwind CDN + vanilla JS) — stat
  cards, progress, latency panel, status histogram, filtered live log with
  redacted detail drawer, payload/headers JSON editors (validate/format/
  reset), profile selector, request preview via the real planner, explicit
  *Save to Config* regeneration with confirmation.
- **Shared core**: `services.prepare_run/execute_prepared` extracted from
  the CLI runner; the GUI starts/stops runs through the identical pipeline
  (inline editor templates ride the same planner via a new `templates=`
  parameter). Pause/resume is deliberately absent — the engine has no safe
  pause primitive.
- **Security posture**: refusing `--host 0.0.0.0` without auth unless
  `--yes-i-know`; no implicit default credentials (auth on + missing creds
  is a startup error); busy port yields an actionable error; graceful
  Ctrl+C cancels active runs and closes sockets.

## 1.3.0 — Project-based workflow (`blazehammer.yaml`, `bh`)

Configuration becomes a reusable project file; CLI flags become temporary
overrides on top of it.

### Added

- **`bh init [NAME]`**: creates `./<name>/` with commented
  `blazehammer.yaml`, example `payload.json`/`headers.json`, `profiles/`,
  `.gitignore`, `.env.example`. Short interactive wizard (all prompts have
  defaults) plus `--non-interactive`, `--name`, `--path`, `--target`,
  `-m/-n/-c`, `--force`.
- **Project discovery**: `run`/`inspect`/`validate` auto-load
  `./blazehammer.yaml`; parent directories are never searched.
  `--config PATH` points at any project from anywhere; payload/header and
  profile paths resolve relative to the YAML file, not the caller's cwd.
- **URL shorthand**: bare `bh` runs the project; `bh <url>` overrides only
  the target; flag-first forms (`bh -n 5`, `bh --config x`) route to
  `run` as well. Positional URL + conflicting `--url` is rejected.
- **`profile create|list|show`** (legacy `profiles` alias kept): minimal
  override-only profile templates; profiles resolve inside the project dir.
- **Environment variables**: new `TARGET`, `METHOD`, `FAKER_LOCALE`,
  `PAYLOAD_FILE`, `HEADERS_FILE`; every variable is accepted under both
  `BLAZE_HAMMER_*` and legacy `BLAZE_*` prefixes (plain prefix wins).
- **YAML validation**: unknown keys rejected with "Did you mean"
  suggestions; syntax errors report line/column; friendly aliases
  (`payload:`/`headers:`/`retries: N`/`faker.{locale,seed}`/
  `output.file`).
- **Zero-config error**: running without any target now says
  "No Blaze Hammer project found" and points at `init`.
- **`bh` console script** installed alongside `blaze-hammer` — same entry
  point, real executable on all platforms.

### Changed

- Merge order is now CLI > env > profile > blazehammer.yaml > defaults
  (previously profile > env > CLI over defaults).
- `inspect` shows the resolved configuration including the project file in
  use and the faker locale.
- New runtime dependency: `pyyaml`.

## 1.2.0 — Faker integration upgrade

Dynamic Faker resolver providing access to the entire installed Faker API
through `{faker.*}` placeholders. Backward compatible with all existing
Faker placeholder syntax.

### Added

- **Dynamic Faker resolution**: `{faker.name}`, `{faker.email}`,
  `{faker.random_int(min=1,max=100)}` — any Faker method callable by name
  without hardcoding.
- **Explicit provider paths**: `{faker.providers.internet.en_US.email}`
  resolved dynamically against the installed Faker.
- **Positional arguments**: `{faker.pystr(10)}`,
  `{faker.random_int(1,100)}` — not restricted to keyword-only.
- **Full argument parsing**: int, float, bool, None, str (single/double
  quoted), list, tuple, dict — safely parsed via `ast.literal_eval`.
- **Native return types**: `{faker.pybool}` → `true` (JSON bool),
  `{faker.random_int(min=1,max=100)}` → `42` (int) when occupying an
  entire value. Embedded usage coerces to string.
- **Global Faker locale**: `--faker-locale bn_BD` sets the default locale
  for all Faker placeholders. Per-placeholder `locale=` overrides.
- **`blaze-hammer faker list [query]`**: browse all Faker providers/methods
  from the CLI, grouped by provider family.
- **`blaze-hammer faker show <method>`**: inspect a Faker method's
  signature, docstring, and sample output.
- **Faker argument validation**: pre-flight checks unknown methods, bad
  kwargs, invalid locales, and missing required arguments.
- **Fuzzy-match suggestions**: misspelled Faker methods show
  "Did you mean: faker.email?"
- **Security guardrails**: blocks `__class__`, `__subclasses__`, `os`,
  `subprocess`, `eval`, and other dangerous attribute names.
- **FakerFactory.default_locale**: factory supports a global default locale
  passed from `--faker-locale`.

### Changed

- `resolve_faker_token` now returns raw native values (int, bool, dict,
  date, etc.) instead of always `str()`. The resolver coerces to text
  only when the token is embedded in a larger string.
- `FakerFactory` accepts `default_locale` parameter.
- `TemplateValidator` accepts `faker_locale` parameter.
- `RunConfig` has new `faker_locale: str | None` field.
- `blaze-hammer faker show` uses `faker.VERSION` (not `__version__`).

## 1.1.0 — Placeholder engine expansion

Thirty new built-in placeholders for API, validation and edge-case testing,
registered through the existing registry (no second resolution system).
Existing placeholders are unchanged.

### Added

- **Type preservation**: placeholders flagged native (`{bool}`, `{null}`,
  `{int}`, `{float}`, `{percent}`, `{price}`, `{negative}`, `{positive}`,
  `{zero}`, `{large_int}`, `{port}`, `{unix}`, `{list}`) return real JSON
  types when they occupy an entire value; embedded usage coerces to JSON-style
  text (`true`, `null`, `[1,2]`).
- Random strings: `{hex(length=16)}`, `{digits(length=6)}`,
  `{letters(length=10, case=mixed|lower|upper)}`,
  `{alphanumeric(length=12)}`.
- Identifiers: `{slug(length=12)}`, `{username(length=10)}`,
  `{token(length=32)}` (cryptographic without `--seed`, deterministic with),
  `{otp(length=6)}`.
- Network: `{ipv4}`, `{ipv6}`, `{port(min=1024,max=65535)}`, `{user_agent}`
  (Faker-backed).
- Date/time: `{datetime(offset=…)}`, `{unix(offset=…)} ` plus relative
  offsets (`s/m/h/d/w`) on `{date}`, `{datetime}`, `{timestamp}`, `{unix}`.
  Malformed offsets fail pre-flight validation.
- Numbers: `{percent(precision=…)}`, `{price(min,max,precision)}`,
  `{negative}`, `{positive}`, `{zero}`, `{large_int(digits=20)}`.
- Edge cases: `{empty_string}`, `{whitespace(length=5)}`,
  `{unicode(length=10)}` (surrogate-safe), `{special_chars(length=10)}`
  (no control characters), `{long_string(length=1000)}`.
- Collections: `{list(item=int(min=1,max=100), length=5)}` producing native
  JSON arrays; nested `list()` up to depth 3; item expressions must not
  contain braces.
- Resource limits with actionable errors: strings ≤100k chars
  (`long_string` ≤1M), lists ≤1000 items, `large_int` ≤1000 digits,
  tokens ≤1024 chars.
- Per-placeholder argument validators attached to registry metadata, so
  every bad argument fails before traffic with messages like
  "Argument 'length' must be a positive integer."
- `blaze-hammer placeholders` now shows return types, and
  `placeholders <name>` renders a full card (arguments, defaults, returns,
  example) generated from the same metadata.

### Changed

- Lone `{int(...)}` / `{float(...)}` values are now emitted as real JSON
  numbers instead of strings (see type preservation above). Embedded usage is
  unchanged. Update test assertions that expected `"42"` from a lone token.
- `{bool}` now yields JSON `true`/`false`; embedded text uses lowercase
  JSON style instead of Python's `True`/`False`.
- `{timestamp}` accepts an optional `offset=` argument (plain `{timestamp}`
  unchanged); `{date}`/new `{datetime}` accept `offset=` alongside the
  existing `format=`.
- AGENTS.md note updated: placeholder string-only rule replaced by the
  full-value/embedded type rule.

## 1.0.0 — Production refactor

Blaze Hammer 1.0.0 reorganizes the codebase into a layered package
(`blaze_hammer/`) with a validated configuration model, a redesigned
placeholder registry, a bounded async execution engine, Rich-free
statistics, and professional CLI/tooling. Existing payloads, header
files, placeholder syntax, custom providers and custom parsers keep
working.

### Added

- Subcommand CLI: `run`, `inspect`, `validate`, `placeholders`,
  `profiles list/show`, `interactive`, `version`, `completion`.
- `--dry-run` and `--preview` / `--preview-count N` sharing the exact
  production request pipeline.
- Pre-flight placeholder validation with did-you-mean suggestions,
  argument coercion checks, Faker attribute/locale probes, file
  existence and env-var checks — before any traffic.
- `--seed`: reproducible generation (scheduler-sequential resolution;
  UUIDs derived from the run RNG).
- Profiles (`profiles/*.json`) with merge priority CLI > `BLAZE_*`
  env vars > profile > defaults.
- `--rate` token-bucket pacing, `-t/--timeout`, opt-in `--retries`
  (exponential backoff, honors `Retry-After`, never retries template
  errors).
- Graceful Ctrl+C/SIGTERM: partial statistics, exit code 130.
- Latency percentiles P50/P90/P95/P99, status histogram, categorized
  failure counts in every report.
- Response filtering (`--status`, `--success-only`, `--failed-only`),
  body truncation (`--max-response-size`), response saving
  (`--save-responses` → summary/responses/errors JSONL), result export
  (`--output FILE.json|FILE.csv`).
- `${VAR}` environment expansion in templates with fail-fast missing-var
  errors; secret redaction by name heuristics + env-derived keys applied
  to previews, prints, exports and logs.
- `blaze-hammer placeholders` documentation generated from the live
  registry, plus Faker method browsing.
- Console script `blaze-hammer`, `python -m blaze_hammer`, packaged
  wheel via hatchling, single-source version from installed metadata.
- Test suite (unit + integration against local HTTP servers, ≥85%
  coverage gate), ruff lint/format, mypy type checking, GitHub Actions CI.

### Changed

- **GET requests now resolve placeholders in headers** (previously headers
  were sent verbatim). GET still ignores payload files.
- Placeholder arguments that fail validation abort the run before it
  starts instead of injecting `[Invalid ...]` strings mid-run.
- Random generators switched from `secrets.choice` to a seeded
  `random.Random` to make `--seed` possible (values remain random-looking;
  unpredictability is not a goal for test data).
- `--file-payload` requires `--post-type form` (multipart cannot combine
  with a JSON body); attachments are validated at startup and closed after
  the run instead of living as module-level open files.
- POST without any payload source now fails fast with an actionable error
  (legacy silently required `payload.json` to exist even for GET runs);
  `--payload` no longer defaults to `payload.json`.
- `utils/custom_parsers.py`, `utils/custom_providers.py` and
  `utils/custom_file_payload.py` moved to `blaze_hammer/ext/`; old paths
  are import shims. `utils/replace_placeholders.py` and
  `utils/compare_json.py` are deprecated shims over the new internals.
- Dependency changes: added `click` and `pydantic`; dropped
  `rich-argparse`; `h2` is now guaranteed via the `httpx[http2]` extra
  (fixing installs that previously failed at runtime with
  `http2=True`). Requires Python ≥ 3.13.

### Fixed

- Unbounded per-request task creation replaced by a fixed worker pool
  with queue backpressure (flat memory for arbitrarily large `-n`).
- Ctrl+C no longer dumps a raw traceback or leaks connections/tasks.
- `h2` missing from dependency metadata broke HTTP/2 for uv/pip installs.
- JSON diff now compares nested structures path-by-path.

### Migration guide (0.x → 1.0)

1. Install once, then prefer `blaze-hammer run ...` over
   `python main.py ...` (the shim remains).
2. Move edits from `utils/custom_*.py` to `blaze_hammer/ext/*.py`.
3. If you relied on `payload.json` being auto-loaded for POST, pass
   `-p payload.json` explicitly (or put it in a profile).
4. If you used `--file-payload` together with JSON bodies, add
   `-pt form`.
5. Malformed placeholders now stop the run at startup; fix them using
   the suggestions from `blaze-hammer validate`.

### Known legacy quirks preserved

- `--json-diff` exits `1` even on success (documented behavior).
