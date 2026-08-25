# AGENTS.md

Blaze Hammer: async API load-testing CLI. All logic lives in `main.py` (argparse + asyncio/httpx engine + rich UI); `utils/` holds the placeholder engine. Python >= 3.13. No tests, no CI, no lint config committed.

## Environment
- Managed with **uv**: `uv sync`, then `uv run python main.py ...` (or `.venv\Scripts\python.exe`). `uv.lock` is gitignored/local-only.
- Dependency sources disagree: `pyproject.toml` (uv's source of truth) vs tracked `requirements.txt` (older pins). Gotcha: `h2` exists only in requirements.txt but is required at runtime — `httpx.AsyncClient(http2=True)` (main.py) fails without it. When adding a dep, update pyproject.toml (and mirror in requirements.txt).

## Running & verifying
- No test suite. Offline smoke test of the placeholder engine:
  `python main.py --json-diff payload_example.json`
  Exits 1 **by design** after printing the diff (`exit(1)` in main.py) — not a failure.
- Real runs hit a live URL. Use `-s/--simple` in headless/non-TTY shells (rich Live UI needs a TTY).
- Placeholders are substituted only for POST (payload + headers). GET sends header values verbatim and ignores the payload file entirely (`make_request` in main.py).

## Extension points
- New built-in `{placeholder}` → dispatch logic in `replace_placeholders()` (`utils/replace_placeholders.py`), helper in `utils/random_functions.py`.
- Custom Faker providers → class in `utils/custom_providers.py`; registered globally at import time in replace_placeholders.py.
- Per-status-code output formatting for `-pp/-pr/-ph` → dicts in `utils/custom_parsers.py`.
- File-upload payloads → `utils/custom_file_payload.py` (`--file-payload`).
- README.md documents every CLI flag and placeholder — update it whenever argparse args or placeholder syntax change.
