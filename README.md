# ⚡ Blaze Hammer

**Blaze Hammer** is an asynchronous API testing and traffic-generation tool built with Python.
It generates dynamic payloads and headers through placeholder injection (Faker + built-ins),
sends configurable asynchronous load over HTTP/1.1 and HTTP/2, and reports production-grade
statistics through a live Rich dashboard or plain CI-friendly output.

> Blaze Hammer is intended for **authorized** API testing: your own services, staging
> environments, load-test endpoints, or systems you have explicit permission to test.
> It deliberately contains no features for bypassing authentication, rate limits,
> CAPTCHAs, WAFs, or other protections.

```text
Blaze Hammer v1.0.0
Target: https://staging.example.com/api/register

--- Final Report ---
Duration: 12.42s
Requests/sec: 161.03
Completed: 2000/2000
Successful: 1987
Failed: 13
Latency ms P50/P90/P95/P99: 41/96/128/210
Status 200: 1987
```

---

## Installation

Requires Python **3.13+**.

```bash
pip install blaze-hammer      # from PyPI, once published
# or from a checkout:
uv sync                        # creates .venv and installs blaze-hammer (editable)
```

Verify:

```bash
blaze-hammer --version         # blaze-hammer 1.3.0
blaze-hammer --help
```

All of these entry points are equivalent — `bh` is a real console script,
not a shell alias:

```bash
bh ...
blaze-hammer ...
python -m blaze_hammer ...
python main.py <URL> ...        # legacy compatibility shim
```

---

## Quick start (project workflow)

The recommended workflow is a project directory driven by `blazehammer.yaml`:

```bash
# Create a project (interactive wizard; --non-interactive for CI)
bh init

# Enter the project
cd my-api-test

# Check everything before sending traffic
bh validate
bh inspect

# Run using blazehammer.yaml (no arguments needed)
bh run          # or simply: bh

# Override the target only - YAML values still apply
bh https://api.example.com/users

# Temporary overrides - the YAML file is never modified
bh https://api.example.com/users -n 1000 -c 50
bh run --rate 200 --timeout 30 --faker-locale bn_BD
```

`init` creates:

```text
my-api-test/
├── blazehammer.yaml   # project configuration (commented, minimal)
├── payload.json       # example payload with placeholders
├── headers.json       # example headers with placeholders
├── profiles/          # reusable per-endpoint configs
├── .gitignore         # .env, results/, __pycache__/
└── .env.example       # documented environment overrides
```

### blazehammer.yaml

```yaml
target: "https://example.com/api"
method: POST
requests: 100
concurrency: 10
delay: 0
payload: payload.json      # relative to this YAML file
headers: headers.json
post_type: json
timeout: 10
retries: 0

faker:
  locale: en_US
  # seed: 12345            # reproducible generated data

output:
  simple: false
  # file: results.json     # export summary (.json/.csv)
```

Unknown keys are rejected with suggestions (`concurency` → *Did you mean:
concurrency?*), and `payload:`/`headers:` paths always resolve relative to
the YAML file itself — so `cd /elsewhere && bh run --config ./proj/blazehammer.yaml`
works exactly like running inside the project.

### Configuration precedence

```text
CLI arguments  >  environment variables  >  profile  >  blazehammer.yaml  >  defaults
```

Environment variables accept both prefixes; the plain form wins if both are set:

| Variable | Maps to |
|---|---|
| `BLAZE_HAMMER_TARGET` / `BLAZE_TARGET` | target URL |
| `BLAZE_HAMMER_METHOD` / `BLAZE_METHOD` | GET/POST |
| `BLAZE_HAMMER_REQUESTS`, `_CONCURRENCY`, `_DELAY`, `_RATE`, `_TIMEOUT`, `_RETRIES`, `_SEED` | numeric fields |
| `BLAZE_HAMMER_FAKER_LOCALE` | faker locale |
| `BLAZE_HAMMER_PAYLOAD_FILE`, `_HEADERS_FILE` | template files |

### URL shorthand

```bash
bh                    # target from blazehammer.yaml
bh <url>              # positional URL overrides only the target
bh <url> -n 5000      # ...plus any other CLI overrides
bh run [URL] [opts]   # explicit form - identical pipeline
```

Passing both a positional URL and `--url` is rejected unless they match.

### Profiles

```bash
bh profile create register        # writes profiles/register.json (override-only template)
bh profile list
bh profile show register
bh run --profile register         # profile layers over blazehammer.yaml
```

Profiles contain only what differs from the base project. The legacy
`profiles list|show` spelling keeps working.

### Classic one-shot usage (unchanged)

```bash
# Dry run: resolve placeholders, build the request, send nothing
blaze-hammer run https://staging.example.com/api/register \
    -m POST -p payload.json --dry-run

# Preview three generated requests, still send nothing
blaze-hammer run ... --preview-count 3

# Send load: 500 requests, 25 concurrent, ~50 req/s
blaze-hammer run https://staging.example.com/api/register \
    -m POST -p payload.json -n 500 -c 25 --rate 50
```

Running without any project or URL fails fast with an actionable message:

```text
No Blaze Hammer project found
create a project with 'blaze-hammer init', pass a URL ('bh https://host/api'), or use --config
```

A typical `payload.json`:

```json
{
  "username": "{faker.user_name}",
  "email": "{email(prefix=user_, length=10)}",
  "password": "{password(length=16, symbols=true)}",
  "plan": "{choice(free, pro)}",
  "trace_id": "{uuid}"
}
```

Every request resolves placeholders freshly; with `--seed` the whole sequence is reproducible.

---

## CLI reference

| Command | Purpose |
| --- | --- |
| `run [URL]` | Execute a load test (also hosts `--dry-run`, `--preview`, `--json-diff`) |
| `inspect [URL]` | Validate configuration and explain it, with one generated request |
| `validate [URL]` | Validate configuration, files and placeholders only |
| `placeholders [QUERY]` | List placeholder syntax; `placeholders faker [sub]` browses Faker |
| `profiles list` / `profiles show NAME` | Manage reusable test profiles |
| `interactive` | Guided configuration flow, then run |
| `version` | Print version (`--version` also works) |
| `completion SHELL` | Print completion script: `bash`, `zsh`, `fish`, `powershell` |

### Run options

Target & body:

| Option | Description |
| --- | --- |
| `-m, --method GET\|POST` | HTTP method |
| `-p, --payload FILE` | JSON payload template (POST body) |
| `--headers FILE` | JSON headers template (all methods) |
| `-dh, --disable-headers` | Ignore the headers file |
| `-pt, --post-type json\|form` | JSON or form-urlencoded body |
| `-fp, --file-payload` | Multipart attachments from `blaze_hammer/ext/attachments.py` |

Load shaping:

| Option | Description |
| --- | --- |
| `-n, --requests N` | Total requests (default 100) |
| `-c, --concurrency C` | Max in-flight requests (default 100) |
| `-d, --delay SECONDS` | Pause between request launches |
| `--rate RPS` | Target throughput via token bucket |
| `-t, --timeout SECONDS` | Per-request timeout (default 30) |
| `--retries N` | Retry connection failures/timeouts/5xx-class statuses |
| `--seed INT` | Deterministic placeholder generation |

Output & diagnostics:

| Option | Description |
| --- | --- |
| `-pp` / `-pr` / `-ph` | Print each payload / response / headers |
| `--status 500,404` | Only print those status codes |
| `--success-only` / `--failed-only` | Print only successes / failures |
| `--max-response-size CHARS` | Truncate printed/saved bodies (default 2000) |
| `--save-responses DIR` | Write `summary.json`, `responses.jsonl`, `errors.jsonl` |
| `--output FILE` | Export summary as `.json` or `.csv` |
| `-s, --simple` | Plain output for CI/pipes (auto-forced when stdout isn't a TTY) |
| `--log-level LEVEL` / `--log-file FILE` | Diagnostics to stderr/file (never stdout) |
| `--debug` | Full tracebacks on errors |
| `--yes` | Skip the large-run confirmation prompt |

Safety valve: runs of ≥ 10,000 requests ask for confirmation on a TTY. Use `--yes`
to skip it in scripts.

### Legacy invocation

Everything below keeps working unchanged:

```bash
python main.py https://host/api -n 200 -c 10 -m POST -pp
python main.py --json-diff payload_example.json     # offline diff mode
blaze-hammer https://host/api -n 200                # bare URL routes to `run`
```

Exit codes: `0` completed · `1` configuration/validation failure · `2` usage error ·
`130` interrupted (Ctrl+C). `--json-diff` intentionally keeps its historical exit code `1`.

---

## Placeholder system

Placeholders are resolved **per request**, inside both payload and header templates.
The authoritative, always-in-sync reference is the registry itself:

```bash
blaze-hammer placeholders        # full table: syntax, return type, description
blaze-hammer placeholders otp    # detail card: arguments, defaults, example
```

### Type preservation

A placeholder occupying an **entire value** keeps its native JSON type; embedded
placeholders become text (JSON-style):

```json
{
  "id": "{int(min=1,max=100)}",     →   "id": 42,
  "active": "{bool}",               →   "active": true,
  "value": "{null}",                →   "value": null,
  "tags": "{list(item=int(min=1,max=9),length=3)}",
                                    →   "tags": [4, 1, 9],
  "message": "user-{int(min=1,max=9)}"
                                    →   "message": "user-7"
}
```

Legacy note: before 1.1.0 even lone `{int(...)}` produced a string. Lone numeric
placeholders now emit real numbers — update assertions in existing test suites
that relied on string values.

### General

| Syntax | Returns | Description |
| --- | --- | --- |
| `{uuid}` | string | UUIDv4 (seed-aware) |
| `{bool}` | boolean | Random true/false |
| `{null}` | null | Real JSON null |
| `{choice(a, b, c)}` | string | Random option |
| `{pick_line(file=words.txt)}` | string | Random line from a text file (cached per run) |

### Random strings

| Syntax | Returns | Description |
| --- | --- | --- |
| `{str(length=16)}` / `{string(...)}` | string | Alphanumeric characters (default length 8) |
| `{hex(length=16)}` | string | Lowercase hexadecimal |
| `{digits(length=6)}` | string | Digits only — leading zeros preserved (`048392`) |
| `{letters(length=10, case=mixed)}` | string | Letters; `case`: mixed/lower/upper |
| `{alphanumeric(length=12)}` | string | Letters + digits |
| `{password(length=12, digits=true, uppercase=true, lowercase=false, symbols=false)}` | string | Password from enabled character sets |

### Identifiers

| Syntax | Returns | Description |
| --- | --- | --- |
| `{slug(length=12)}` | string | URL-safe lowercase slug — letters, digits, hyphens |
| `{username(length=10)}` | string | `user_`-prefixed username |
| `{token(length=32)}` | string | Auth-style token. Without `--seed` it uses cryptographic randomness; **with `--seed` it becomes deterministic** (reproducibility over strength — a deliberate, documented trade-off for test data) |
| `{otp(length=6)}` | string | Numeric OTP; leading zeros preserved |

### Network

| Syntax | Returns | Description |
| --- | --- | --- |
| `{ipv4}` / `{ip}` | string | Valid IPv4 address |
| `{ipv6}` | string | Valid IPv6 address |
| `{port(min=1024, max=65535)}` | integer | Port number (defaults to the valid 1–65535 range) |
| `{user_agent}` | string | Realistic User-Agent via Faker |

### Date/time

Offsets use `s`, `m`, `h`, `d`, `w` units and apply everywhere shown below
(`{date(offset=-7d)}`, `{datetime(offset=+30m)}`, `{timestamp(offset=-1h)}`,
`{unix(offset=-1h)}`). Malformed offsets fail validation.

| Syntax | Returns | Description |
| --- | --- | --- |
| `{date(offset=-7d, format=%Y-%m-%d)}` | string | strftime date (default today) |
| `{datetime(offset=+7d, format=%Y-%m-%dT%H:%M:%S)}` | string | Full datetime |
| `{timestamp(offset=-1h)}` | string | UNIX timestamp (legacy string type preserved) |
| `{unix(offset=-1h)}` | integer | UNIX timestamp as a native integer |

### Numbers

| Syntax | Returns | Description |
| --- | --- | --- |
| `{int(min=1, max=100)}` | integer | Random integer in range |
| `{float(min=0, max=1, precision=2)}` | number | Rounded float |
| `{percent(precision=2)}` | integer/number | 0–100; integer unless precision given |
| `{price(min=10, max=500, precision=2)}` | number | Non-negative price-like float |
| `{negative}` / `{positive}` | integer | ±1..1,000,000 |
| `{zero}` | integer | Literal `0` |
| `{large_int(digits=20)}` | integer | Huge integer for boundary tests (≤1000 digits) |

### Edge cases

Built for validation/limit testing:

| Syntax | Returns | Description |
| --- | --- | --- |
| `{empty_string}` | string | `""` |
| `{whitespace(length=5)}` | string | Exactly N spaces (0 allowed) |
| `{unicode(length=10)}` | string | Multi-script sample data; never lone surrogates, so JSON encoding is safe |
| `{special_chars(length=10)}` | string | Punctuation/symbols — no control characters that would corrupt terminals or JSON |
| `{long_string(length=1000)}` | string | Exactly N filler chars (hard cap 1,000,000) |

### Collections

| Syntax | Returns | Description |
| --- | --- | --- |
| `{list(item=int(min=1,max=100), length=5)}` | array | Native JSON array; item resolved fresh each element. Nested `list()` works up to depth 3. Item expressions must not contain braces — write `item=faker.word`, not `item={faker.word}`. Length is capped at 1000. |

### Resource limits

To keep large runs safe, argument caps are enforced with clear errors:
strings ≤ 100,000 chars (`long_string` ≤ 1,000,000), lists ≤ 1,000 items,
`large_int` ≤ 1000 digits, tokens ≤ 1024 chars. Invalid arguments always fail
validation before any traffic — e.g. `{otp(length=0)}` reports
*"Argument 'length' must be a positive integer."*

### Faker

Essentially the entire installed Faker API is available. The resolver dynamically
discovers methods from the Faker instance — no hardcoded provider list.

```text
{faker.name}
{faker.email}
{faker.city}
{faker.random_int(min=1,max=100)}
{faker.pybool}
{faker.date_of_birth(minimum_age=18, maximum_age=65)}
```

#### Explicit provider paths

Both short and full provider paths work:

```text
{faker.name}
{faker.providers.person.en_US.name}
{faker.providers.internet.en_US.email}
```

#### Arguments

Keyword and positional arguments are supported. Values are safely parsed as
Python literals (int, float, bool, None, str, list, tuple, dict):

```text
{faker.pystr(min_chars=10, max_chars=20)}
{faker.random_int(min=1, max=100)}
{faker.date(pattern="%Y-%m-%d")}
{faker.pystr(10)}
```

#### Native return types

When a faker token occupies an entire value, the native JSON type is preserved:

```json
{
  "id": "{faker.random_int(min=1,max=100)}",
  "active": "{faker.pybool}",
  "name": "{faker.name}"
}
```

→

```json
{
  "id": 42,
  "active": true,
  "name": "John Smith"
}
```

Embedded usage produces strings: `"user-{faker.random_int(min=1,max=999)}"` → `"user-421"`.

#### Locale support

Per-placeholder locale override:

```text
{faker.name(locale=fr_FR)}
{faker.name(locale=bn_BD)}
```

Global default via CLI:

```bash
blaze-hammer run URL --faker-locale bn_BD
```

The global locale applies when no per-placeholder `locale=` is specified.

#### Custom providers

Custom providers in `blaze_hammer/ext/providers.py` are automatically available:

```text
{faker.simple_example(category=greetings)}
{faker.advanced_example(category=greetings, language=bn, length=5)}
```

#### Seeded generation

`--seed` applies to Faker instances. Identical seeds produce identical sequences:

```bash
blaze-hammer run URL --seed 12345
```

#### Discovery and inspection

```bash
blaze-hammer faker list           # all available methods
blaze-hammer faker list email     # filtered
blaze-hammer faker show random_int  # signature, docs, sample
```

Browse what's available: `blaze-hammer placeholders faker city`.

### Environment variables

`${VAR}` references expand once per request generation and fail fast at validation
time if unset. Values that came from environment variables are treated as secrets
for display purposes:

```json
{ "Authorization": "Bearer ${API_TOKEN}" }
```

### Validation & helpful errors

Before any traffic is sent, every token is checked against the registry, arguments are
coerced exactly as they would be at runtime, Faker attributes/locales are probed, and
referenced files must exist:

```text
error: Invalid placeholder(s) found
  [x] payload.json:3
  {emal(prefix=x_)}
  Unknown placeholder
  Did you mean: email?
```

Unknown tokens are never silently sent as literal strings like `[Invalid faker field: x]`
(that was legacy behavior); they abort the run before request #1.

---

## Seeded randomness

```bash
blaze-hammer run ... --seed 12345
```

Guarantee: **the same seed plus identical templates produces the identical request
sequence** — UUIDs, strings, choices, Faker values, picked lines, all of it. This works
because only the scheduler task generates requests, strictly sequentially, regardless of
network timing.

Not deterministic (by nature): `{timestamp}` and `{date}` reflect wall-clock time.

---

## Profiles

A profile is a JSON file holding any subset of run configuration:

```json
// profiles/register.json
{
  "target": "https://staging.example.com/api/register",
  "method": "POST",
  "payload_file": "payload.json",
  "requests": 5000,
  "concurrency": 40,
  "rate": 100,
  "retries": {"max_retries": 2}
}
```

```bash
blaze-hammer run --profile register            # profiles/register.json
blaze-hammer run --profile register -n 9000    # CLI wins
blaze-hammer inspect --profile register        # validate & explain
```

Merge priority, highest first:

```text
CLI arguments → BLAZE_* environment variables → profile file → defaults
```

Supported env vars: `BLAZE_REQUESTS`, `BLAZE_CONCURRENCY`, `BLAZE_DELAY`, `BLAZE_RATE`,
`BLAZE_TIMEOUT`, `BLAZE_RETRIES`, `BLAZE_SEED`. Unknown profile keys are rejected
(typo protection).

---

## Secrets & redaction

Header/payload keys whose names look sensitive (`authorization`, `cookie`, `api-key`,
`token`, `password`, … — case-insensitive substring match) plus any key whose value came
from `${VAR}` expansion are replaced with `***REDACTED***` in previews, `-pp/-pr/-ph`
output, saved responses, and logs. Add project-specific names via a profile's
`sensitive_keys` array.

Diagnostics (`--log-level DEBUG`, `--log-file`) go to stderr or a file, never into the
Rich output stream.

---

## Rate limiting, concurrency, retries

- **concurrency** caps simultaneous requests (bounded worker pool + bounded queue;
  memory stays flat no matter how large `-n` is).
- **rate** paces request *starts* via a lazy token bucket (~R req/s, capacity 1 =
  smooth sending). Combine freely: `--rate 100 -c 20` means "at most 20 in flight,
  ~100 started per second".
- **delay** adds an extra pause between launches; shutdown interrupts it instantly.
- **retries** are opt-in. Connection failures, DNS/TLS errors and timeouts retry with
  exponential backoff (`base 0.5s`, cap `8s`). Statuses `502/503/504` retry; `429`
  retries honoring `Retry-After` (capped at 30 s). Retries count separately in stats
  and respect rate pacing and Ctrl+C.

Timeout classification feeds the failure report: `timeout`, `connection`, `dns`,
`tls`, `template`, `other`.

---

## Graceful shutdown

Ctrl+C (or SIGTERM on POSIX) stops new work immediately, lets in-flight requests finish,
and prints partial statistics marked as interrupted. Exit code `130`.

---

## Statistics & export

The engine collects Rich-free statistics: totals, retries, latency percentiles
(P50/P90/P95/P99), status-code histogram, and categorized failure counts with samples.

```bash
blaze-hammer run URL --save-responses results/   # summary.json + responses.jsonl + errors.jsonl
blaze-hammer run URL --output results.json       # machine-readable summary
blaze-hammer run URL --output results.csv        # one-row CSV for spreadsheets/CI
```

`responses.jsonl` records redacted resolved payloads/headers and size-capped bodies
per request; failures land in `errors.jsonl`.

---

## Web API server (headless)

`bh web` starts a **backend-only** FastAPI + WebSocket server over the same
project and engine as the CLI. The Python package contains no web UI — the
dashboard is a separate React project that consumes this API.

```bash
bh web                 # http://127.0.0.1:8080 (defaults from blazehammer.yaml)
bh --web               # identical shortcut
blaze-hammer web --port 9000
```

Startup banner:

```text
Blaze Hammer API  v1.5.0

  Project:       ./blazehammer.yaml
  Server:        http://127.0.0.1:8080
  API:           http://127.0.0.1:8080/api/v1
  WebSocket:     ws://127.0.0.1:8080/api/v1/ws
  Docs:          http://127.0.0.1:8080/docs
  Authentication: enabled
```

### REST surface (`/api/v1`)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/health` | public | liveness probe |
| GET | `/api/v1/info` | public | name/version/features |
| POST | `/api/v1/auth/login` | public | session cookie login |
| POST | `/api/v1/auth/logout` | session | drop session |
| GET | `/api/v1/auth/me` | session | current user |
| GET | `/api/v1/config` | session | resolved run config (no secrets) |
| GET | `/api/v1/config/templates` | session | editor text + SHA-256 revisions |
| POST | `/api/v1/config/templates/save` | session+CSRF | atomic template save (422 invalid JSON / 409 conflict) |
| GET | `/api/v1/placeholders/catalog` | session | autocomplete metadata: built-ins + dynamic Faker discovery |
| POST | `/api/v1/config/save` | session+CSRF | PATCH-style YAML update (round-trip safe) |
| GET | `/api/v1/profiles[/{name}]` | session | list/show profiles |
| POST | `/api/v1/runs` | session+CSRF | start run → `{run_id, status}` |
| GET | `/api/v1/runs[/{id}]` | session | history / single run |
| POST | `/api/v1/runs/{id}/stop` | session+CSRF | cooperative stop |
| DELETE | `/api/v1/runs` | session+CSRF | clear history |
| GET | `/api/v1/runs/{id}/log` | session | response snapshots per request (bounded) |
| POST | `/api/v1/preview` | session+CSRF | resolve sample requests |
| POST | `/api/v1/validate` | session+CSRF | full pre-flight check |

Errors use one envelope: `{"error": {"code": "NOT_AUTHENTICATED",
"message": "…"}}`. Mutating endpoints require the `X-Requested-With:
XMLHttpRequest` header (CSRF guard).

### WebSocket `ws://…/api/v1/ws`

Session-cookie authenticated; stable JSON events:

```json
{"type": "run.started",   "run_id": "abc123", "requested": 1000}
{"type": "stats.updated", "run_id": "abc123", "completed": 420, "success": 415,
 "failed": 5, "rps": 82.4, "latency_ms": {"p50": 118, "p95": 240}}
{"type": "request.completed", "run_id": "abc123", "seq": 7, "index": 6,
 "ok": true, "status": 200, "latency_ms": 124}
{"type": "run.completed", "run_id": "abc123", "status": "completed"}
```

Multiple simultaneous clients are supported; state lives server-side, so a
page refresh recovers via `GET /api/v1/runs`.

### Configuration PATCH

`POST /api/v1/config/save` updates only the fields you send — it never
rewrites the whole file. Comments, key order, quoting, blank lines and
unknown/custom keys survive via round-trip YAML (ruamel). Send
`config_revision` (from `GET /api/v1/config`) for optimistic concurrency:
mismatches get `409 CONFIG_CONFLICT` with `current_revision`. Explicit
`null` clears an optional value; omitted means unchanged. Empty patches
rewrite nothing. Successful changes broadcast `config.changed` with only
the changed field names.

### Response snapshots

Every request stores a bounded snapshot: status, monotonic-timed latency,
allowlisted response headers (`content-type`, `content-length`, `server`,
`location`, …), wire `body_size`, and a size-capped body excerpt with an
explicit truncation flag. Binary responses are never read or decoded
(`"[binary response omitted]"`); undecodable text becomes
`"[unable to decode response body]"`.

```yaml
response_logging:
  mode: errors        # none | errors | all   (default: errors)
  max_body_bytes: 4096
  max_headers: 20
  # allow_headers: [content-type, server]   # overrides the default allowlist
  # redact_keys: [password, token]          # JSON keys masked in excerpts
```

CLI: `--response-log all --response-body-limit 8192`. Bodies are never read
at all under `mode: none`; caps apply in every mode. Successful saves of the
config PATCH preserve this section like any other.

### Template persistence & revisions

Editors load text **plus a `payload_revision`/`headers_revision`** (SHA-256 of
the file contents). Saves send the revision they loaded:

```json
POST /api/v1/config/templates/save
{ "payload": "{\n  \"id\": \"{uuid}\"\n}", "payload_revision": "1ba045…" }
```

```json
{ "ok": true, "saved": ["payload"], "payload_revision": "b2c70a…", "headers_revision": null }
```

- Only supplied fields are written; formatting is preserved verbatim (a
  trailing newline is added), placeholders untouched.
- Malformed JSON → `422 INVALID_JSON` with file/line/column.
- File changed since load → `409 TEMPLATE_CONFLICT` echoing
  `current_revision`; never silently overwritten.
- Writes are atomic (tmp → fsync → rename). On success all connected
  WebSocket clients receive `{"type": "config.changed", "changed":
  ["payload"]}` (names only, never contents).

### Placeholder catalog

`GET /api/v1/placeholders/catalog` returns built-in entries from the real
registry plus every Faker method discovered dynamically from the installed
Faker (incl. custom providers in `blaze_hammer/ext/providers.py`), grouped by
provider family with signature-derived parameters — cached per locale,
invalidated on configuration changes. The same frame is pushed as a
`placeholder.catalog` WebSocket event right after `hello`.

### Configuration

```yaml
web:
  enabled: true
  host: "127.0.0.1"
  port: 8080            # 0 = auto-assign
  auth:
    enabled: true
    username: admin
    password_hash: "scrypt$..."   # or plaintext `password:` (hashed in memory)
  cors:
    enabled: true
    origins: ["http://localhost:5173"]
```

Precedence is the standard chain — CLI > env (`BLAZE_HAMMER_WEB_*`,
`BLAZE_WEB_*`, `BH_WEB_*`) > YAML > defaults. Authentication is enforced
server-side on every route **and** the socket; with auth enabled but no
credentials configured the server refuses to start.

### Development workflow (separate frontend repo)

```bash
# Terminal 1 — backend
cd blaze-hammer && bh web

# Terminal 2 — frontend
cd blaze-hammer-web && npm run dev     # talks to http://127.0.0.1:8080
```

## Interactive mode

```bash
blaze-hammer interactive
```

Prompts for target, method, volume and templates, validates everything through the same
pipeline, optionally shows a preview, then runs.

---

## JSON diff (offline)

Resolve placeholders without sending anything and inspect what changed:

```bash
blaze-hammer run --json-diff payload.json       # or: python main.py --json-diff payload.json
blaze-hammer validate -p payload.json           # checks only
```

Nested structures are compared path-by-path (`$.order.items[0].price`), replacing the old
top-level-only table.

---

## Architecture

```text
CLI (click)  →  Configuration (pydantic RunConfig)  →  Validation
            →  Template resolution (registry + Faker bridge)
            →  Request planning (single pipeline shared by preview/dry-run/run)
            →  Execution engine (scheduler + bounded worker pool, rate bucket, retries)
            →  HTTP client (one shared httpx.AsyncClient, HTTP/2)
            →  Statistics collector (Rich-free)
            →  Output (live dashboard / simple printer, exporters)
```

Design rules worth knowing before hacking:

- One request pipeline: preview, dry-run, and real execution all call
  `RequestPlanner.next_plan()`.
- The core engine never imports Rich; the dashboard polls `StatsCollector` snapshots.
- The scheduler alone resolves templates — this is what makes `--seed` exact.
- User-editable extension points live in `blaze_hammer/ext/`:
  - `parsers.py` – per-status-code output formatting dicts
  - `providers.py` – drop-in Faker providers (auto-registered)
  - `attachments.py` – static multipart attachments
- The old `utils/custom_*.py` import paths remain as shims.

See [CHANGELOG.md](CHANGELOG.md) for migration notes from 0.x.

---

## Development

```bash
uv sync --dev                  # install with dev tools
uv run ruff check .            # lint
uv run ruff format .           # format
uv run mypy                    # type check
uv run pytest                  # unit + integration tests (local server only, no internet)
uv run pytest --cov=blaze_hammer
uv build                       # build wheel/sdist
```

Tests spin up throwaway local HTTP servers; nothing touches the network. CI runs the
same gates via GitHub Actions.

---

## Security considerations

- Only point Blaze Hammer at systems you own or are authorized to test.
- Large runs require explicit confirmation interactively; scripts should use `--yes`
  consciously.
- There is no IP rotation, fingerprint evasion, CAPTCHA solving, or auth bypass — by design.
- Treat payload/header templates as code: `${ENV}` expansion means the process
  environment can inject credentials; use `sensitive_keys` to keep them out of reports.
