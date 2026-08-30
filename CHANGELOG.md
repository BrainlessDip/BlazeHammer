# Changelog

## 1.10.0 — Expanded body / post-type system

`post_type` is no longer just `json` or `form`. Eight body encodings are now
supported, each with auto-injected `Content-Type`, placeholder resolution
across all text types, and consistent integration with the planner, runner,
Web GUI, CLI, and sample/log pipelines.

### Added

- **Eight `PostType` values**: `none`, `json`, `form`, `multipart`, `raw`,
  `xml`, `html`, `binary` (was just `json` / `form`).
- **`PostType._missing_` (case-insensitive)**: `XML` / `Xml` / `xml` all
  resolve to `PostType.XML`; `PostType.allowed_values()` exposes the
  sorted set for CLI / OpenAPI.
- **`PostType.default_content_type`**: built-in `Content-Type` per type
  (`application/json`, `text/plain`, `application/xml`, `text/html`,
  `application/octet-stream`, etc.). The runner injects it whenever the
  user-supplied headers do not already specify one.
- **Raw text bodies** (`raw`, `xml`, `html`): the payload can be either a
  JSON-string or a structured object — structured payloads are
  serialised to JSON for the body bytes. Multipart keeps the
  `form_data` path (with `data=...` to httpx) so file attachments still
  work through `--file-payload`.
- **Binary bodies** (`binary`): the payload is encoded to UTF-8 bytes and
  sent via httpx `content=...`.
- **Runner content routing** (`engine/runner.py:_execute`): dispatches
  `json`, `data`, or `content` httpx kwargs based on `plan.post_type`,
  with default `Content-Type` injection and a `post_type=none` escape
  hatch that suppresses body kwargs even on POST/PUT/PATCH/DELETE.
- **Validation** (`config/validation.py`): `post_type=none` bypasses the
  "POST with a body requires --payload" check (a `none` POST has no
  body to configure). `file_payload` now requires `form` or `multipart`.
- **API model** (`web/models.py`): `SaveConfigRequest.post_type` and
  `RunSettingsPayload.post_type` accept the full eight-value set.
- **CLI** (`cli/options.py`): `-pt/--post-type` choices expanded to the
  full eight-value set; help text updated.
- **46 new tests** in `tests/unit/test_body_types.py` covering enum
  membership and case-insensitivity, planner routing per type, all HTTP
  methods × all body types, default `Content-Type` injection, preview,
  body-preview property, and validation edge cases.

### Changed

- **`RequestPlan` dataclass** (`engine/planner.py`): adds `raw_body`,
  `body_bytes`, `content`, and `default_content_type` fields. The
  `body_preview` property now returns the correct body type per
  encoding (dict for json/form/multipart, str for raw/xml/html,
  `[binary N bytes]` marker for binary).
- **`_record_outcome`** (`web/runs.py`): now handles non-dict
  `resolved_payload` (raw/xml/html) by storing the string as-is rather
  than re-running `redact_mapping` (which expects a `Mapping`).
- **`SampleStore._build_sample`** (`web/samples.py`): same non-dict
  handling for request body text in samples.
- **Preview endpoint** (`web/routes/runs_routes.py`): wraps non-dict
  `body_preview` results into `{"_raw": "<text>"}` so the structured
  `PreviewPlan.body` remains valid for the React frontend.

## 1.9.0 — Request/response logging fix

Fixes `request_headers`, `request_body`, `request_cookies`, and
`response_body_excerpt` always being `null` in WebSocket events and run logs.
These fields now always contain the actual resolved data used by each
outgoing request.

### Fixed

- **`response_body_excerpt` always null**: `response_snapshot_fields` now
  populates the excerpt whenever a body exists and `response_logging.mode`
  is not `none` — the mode previously gated excerpt population for the
  log endpoint, causing `response_body_excerpt: null` even when
  `body_size > 0`.
- **WS event missing `request_cookies`**: `request.completed` events now
  include the parsed/redacted cookies from the `Cookie` header.
- **`headers` → `response_headers`**: log entries and WS events now use
  the canonical `response_headers` field (previously `headers`). The
  endpoint falls back to `headers` for entries written by older versions.

### Changed

- **`ResponseSnapshot` model** (`web/models.py`): `headers` field renamed
  to `response_headers`; `request_cookies` added.
- **`run_log` endpoint**: reads `response_headers` from log entries with
  fallback to `headers` for backward compatibility.
- **13 new tests** in `test_response_snapshots.py`: GET with headers,
  POST with JSON, inline headers, Faker payload resolution, placeholder
  header resolution, GET without body, sensitive header redaction,
  response body excerpt population, large response truncation,
  response_headers in log, request_cookies in log, concurrent request
  isolation, WS event request data.

## 1.8.0 — Structured request/response samples

Representative request/response records persisted per run, with separate
request headers/body/cookies, response headers/body, configurable body
limits, and a dedicated API endpoint.

### Added

- **`SampleOptions`** config model (`config/models.py`): `enabled`,
  `max_per_run` (default 20), `max_request_body_size` (default 64KB),
  `max_response_body_size` (default 64KB). Lives under the `samples:` key
  in `blazehammer.yaml` and the normal merge pipeline.
- **`SampleStore`** (`web/samples.py`): collects bounded representative
  samples per run — first request, first success, first failure, one per
  distinct HTTP status code (up to `max_per_run`). Body excerpts are
  truncated at the configured byte cap; JSON payloads are parsed when
  possible.
- **`RequestSample`** Pydantic model (`web/models.py`): typed response for
  the new API endpoint — structured `request` (method, url, headers, body,
  cookies) and `response` (status_code, headers, body, content_type, size,
  truncated) sub-models, plus `id`, `timestamp`, `reason`, `duration_ms`,
  `ok`, `attempts`, `error`, `error_category`.
- **`GET /api/v1/runs/{id}/samples`** endpoint: returns the full list of
  representative samples for a run.
- **`sample_count`** field added to `RunSummary` and `GET /runs/{id}`
  responses.
- **Cookie extraction** (`engine/runner.py`): `RequestOutcome` now carries
  `resolved_cookies` parsed from the `Cookie` request header. Cookies are
  redacted via `redact_mapping` before storage.
- **`request_cookies`** field in `ResponseSnapshot` log entries.
- **30 new tests** in `tests/unit/test_samples.py`: truncation, body
  excerpt, classification logic (first/first_success/first_failure/
  distinct status), redaction (cookies, headers, payload), truncation of
  request and response bodies, live API integration (endpoint, summary,
  disabled mode, unknown run, failures, max_per_run, cookies).

### Changed

- **`RunHandle`** (`web/runs.py`): gains `sample_store` field; lazily
  initialised on first outcome when `cfg.samples.enabled`. `_record_outcome`
  feeds every outcome to the store.
- **`response_snapshot_fields`**: no longer the sole response capture path;
  samples carry their own body structures independently.
- **`RunConfig.needs_bodies`**: unchanged — body reads still gated by
  `response_logging.mode`; sample body capture piggy-backs on the same read.

### Removed

- None.

## 1.7.0 — Full HTTP method support

All nine standard HTTP methods are now first-class citizens, replacing the
hard-coded GET/POST bifurcation with a single generic pipeline.

### Added

- **All standard HTTP methods**: GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS,
  CONNECT, TRACE — each accepted via CLI (`--method`), YAML config, and API
  (`POST /api/v1/runs`, `/preview`, `/validate`, `/config/save`).
- **`Method` enum** (`config/models.py`): single source of truth with
  case-insensitive lookup, `.supports_body` property, and
  `Method.allowed_values()` for CLI help / OpenAPI exposure.
- **HEAD** requests never attempt body reads; response is status + headers +
  timing only.
- **CONNECT / TRACE** pass through to the HTTP transport; transport-level
  rejections report the real HTTP error rather than a Blaze Hammer internal
  error.
- **WebSocket `request.completed`** events now include the `method` field.

### Changed

- **Request planner** (`engine/planner.py`): body attachment is driven by
  `Method.supports_body` instead of `if method == "POST"`. Body-capable
  methods (POST, PUT, PATCH, DELETE) with a payload get the resolved body
  attached; bodyless methods never do.
- **Runner** (`engine/runner.py`): request kwargs are built generically for
  all methods — no per-method `if/elif` chains.
- **Validation** (`config/validation.py`): "requires payload" check applies to
  all `supports_body` methods, not just POST.
- **Preview warning**: "No payload configured" now fires for any body-capable
  method, not only POST.
- CLI `--method` choices and init prompt now list all nine methods.

### Fixed

- CLI `--method` parsing is fully case-insensitive (e.g. `--method patch`
  resolves to PATCH).
- `SaveConfigRequest` regex updated to accept all nine method values; the
  PATCH-style `/config/save` endpoint no longer rejects valid methods.

## 1.6.0 — Response snapshots & run history

Every completed request now stores a bounded response snapshot alongside the
existing statistics, exposed through the run-log API and WebSocket events.

### Added

- **Response snapshots**: status code, monotonic-timed `response_time_ms`,
  allowlisted `headers`, `content_type`, wire-accurate `body_size`, and a
  size-capped `response_body_excerpt` with an explicit
  `response_body_truncated` flag.
- **`response_logging:` config section** (`mode: none|errors|all` — default
  `errors`; `max_body_bytes: 4096`; `max_headers: 20`;
  `allow_headers:`/`redact_keys:` overrides) with CLI flags
  (`--response-log`, `--response-body-limit`) and env vars
  (`BLAZE[_HAMMER]_RESPONSE_LOG`, `_RESPONSE_BODY_LIMIT`). `none` skips
  body reads entirely; caps bound memory/storage in every mode.
- **Binary safety**: image/audio/video/octet-stream/zip/pdf responses are
  never read or decoded — the connection closes immediately and the excerpt
  becomes `[binary response omitted]`.
- **Charset handling**: excerpts decode using the declared charset
  (fallback UTF-8); undecodable payloads become
  `[unable to decode response body]`. Runs never crash on bad bytes.
- **Optional JSON key redaction** inside excerpts via `redact_keys`
  (off by default; only parses small JSON bodies).
- **Run summary aggregates**: `average/min/max_response_time_ms` and
  `status_codes` on `GET /runs/{id}` (additive).
- **Typed log API**: `GET /runs/{id}/log` now returns `ResponseSnapshot`
  models (same entries, documented shape).

### Fixed

- Runner marked every completed HTTP exchange as success regardless of
  status; non-2xx/3xx responses now count as failed in stats and summaries.

### Changed

- WebSocket `request.completed` events gained optional
  `content_type`/`body_size`/excerpt fields (excerpt clamped to 1024 chars,
  present only when the logging mode stores it).

## 1.5.0 — Headless API server (frontend split)

The Python package now ships **only the backend**: core + CLI + REST/WebSocket
API. The web UI moves to a separate React project that consumes this server.
No HTML, JS, CSS or static assets are served anymore.

### Changed

- **API-only server**: `bh web` / `bh --web` start the FastAPI+uvicorn
  backend; the startup banner prints Server/API/WebSocket/Docs URLs and no
  longer offers `--open`. `GET /` returns a JSON service descriptor instead
  of a page.
- **Versioned surface**: all routes now live under `/api/v1/...`
  (`health`, `info`, `auth/*`, `config*`, `profiles*`, `runs*`,
  `preview`, `validate`); the WebSocket is `/api/v1/ws`.
- **Consistent error envelope**: every HTTP error returns
  `{"error": {"code": "…", "message": "…"}}` with stable machine codes
  (`NOT_AUTHENTICATED`, `VALIDATION_ERROR`, `RATE_LIMITED`, …); validation
  failures report field paths without leaking internals; unexpected
  exceptions become opaque `INTERNAL_ERROR`.
- **CORS**: `web.cors.origins:` (alias of `allow_origins:`) configures
  browser origins for the split deployment; development defaults cover
  localhost React/Vite ports; credentials-aware (no wildcard).
- **Security headers** tightened for an API (`default-src 'none'`);
  `/docs` and `/redoc` remain session-gated.

### Added

- `GET /api/v1/info`: name/version/api-version/feature flags for clients.
- `POST /api/v1/validate`: full pre-flight (config, files, placeholder
  resolution) over inline templates or project files — never sends traffic.
- **Template save API**: `GET /api/v1/config/templates` now returns
  SHA-256 `payload_revision`/`headers_revision` plus project-relative paths;
  new `POST /api/v1/config/templates/save` persists editor content with
  JSON-syntax validation (422 with line/column), optimistic concurrency
  (409 `TEMPLATE_CONFLICT` echoing `current_revision`), atomic tmp→fsync→
  rename writes, partial saves, formatting/placeholder preservation, and a
  `config.changed` WebSocket broadcast (names only). Backed by the reusable
  `blaze_hammer/files/templates.py` service.
- **Placeholder catalog**: `blaze_hammer/templating/catalog.py` derives
  built-in metadata from the live registry and Faker entries dynamically
  from the installed Faker + custom providers (signature-inspected,
  side-effect-free, cached per locale). Exposed via
  `GET /api/v1/placeholders/catalog` and pushed as a
  `placeholder.catalog` WebSocket event after `hello`.
- **Config PATCH API**: `POST /api/v1/config/save` is now a field-level
  update — round-trip YAML editing (ruamel) preserves comments, ordering,
  quoting and unknown/custom keys; only explicitly provided fields change
  (`model_fields_set` semantics, explicit `null` supported for optionals);
  nested `web.{host,port,enabled}` merges in place. Adds
  `config_revision` (GET /config + conflict gate → 409 `CONFIG_CONFLICT`),
  atomic writes, no-op patches that never touch the file, and
  `config.changed` broadcasts listing exactly the changed field names.
- Backend contract documentation in the README (endpoints, auth, WS event
  protocol, error format, two-terminal dev workflow).

### Removed

- `blaze_hammer/web/templates/` and `blaze_hammer/web/static/` plus their
  serving routes; the embedded dashboard is superseded by the external
  React frontend.

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
