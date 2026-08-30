"""Placeholder catalog: editor metadata derived from the live backend.

Single source of truth for autocomplete/hover documentation consumed by the
Web GUI. Everything here is *derived* at runtime:

- built-ins come from the real ``PlaceholderRegistry`` specs (dispatch order,
  syntax, docs, argument names);
- Faker entries come from the actually installed Faker version, including
  custom providers registered through :mod:`blaze_hammer.ext.providers`;
- provider paths mirror the resolvable ``faker.providers.<family>.<method>``
  token form.

No placeholder is ever executed to build the catalog (discovery must not have
side effects); signatures are inspected via :func:`inspect.signature`.

Results are cached per locale; call :func:`invalidate_catalog_cache` after
changing provider/locale configuration.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

from blaze_hammer.templating.faker_bridge import FakerFactory
from blaze_hammer.templating.registry import PlaceholderRegistry

#: Catalog schema version; bump when entry shape changes.
CATALOG_VERSION = 1

#: Non-method attributes on the Faker instance that must never surface.
_FAKER_EXCLUDED = frozenset(
    {
        "seed",
        "seed_instance",
        "random",
        "json_encoder",
        "factories",
        "providers",
        "locales",
        "locale",
        "country_codes",  # deprecated alias handled below if absent
    }
)

#: Argument-doc lines look like ``min  integer`` / ``prefix: string`` /
#: ``max = 10``; a wide gap or explicit separator distinguishes them from
#: running prose.
_ARG_LINE_RE = re.compile(r"^[ \t]*-?[ \t]*(\w[\w/]*)(?:[ \t]{2,}|[=:])", re.MULTILINE)

#: Per-locale catalog cache (and pre-built WS frames under an "event" key).
_CATALOG_CACHE: dict[Any, dict[str, Any]] = {}


def _first_line(text: str) -> str:
    """First human-readable docstring line.

    Faker method docs are frequently *only* ``:example:`` directives; in that
    case surface the example content instead of returning an empty string.
    """
    fallback = ""
    for line in (text or "").splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        if cleaned.startswith(":"):
            if cleaned.startswith(":example:") and not fallback:
                remainder = cleaned[len(":example:") :].strip().strip("'\"")
                if remainder:
                    return f"e.g. {remainder}"
            if not fallback:
                fallback = cleaned
            continue
        return cleaned
    return fallback


def _spec_parameters(arguments: str) -> list[dict[str, str]]:
    """Extract parameter names from a spec's free-form arguments doc."""
    names: list[str] = []
    for match in _ARG_LINE_RE.finditer(arguments or ""):
        for part in match.group(1).split("/"):
            part = part.strip()
            if part and part not in ("example", "returns") and part not in names:
                names.append(part)
    return [{"name": name} for name in names]


def _builtin_entries(registry: PlaceholderRegistry) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for spec in registry.specs():
        insert = spec.syntax or f"{{{spec.keyword}}}"
        entries.append(
            {
                "name": spec.keyword,
                "kind": "builtin",
                "description": _first_line(spec.description),
                "syntax": spec.syntax or insert,
                "insert_text": insert,
                "returns": spec.returns,
                "example": spec.example,
                "parameters": _spec_parameters(spec.arguments),
            }
        )
    return entries


def _signature_parameters(info: dict[str, Any] | None) -> list[dict[str, str]]:
    if not info:
        return []
    params: list[dict[str, str]] = []
    for raw in info.get("params", []):
        entry = {"name": raw["name"], "kind": raw.get("kind", "POSITIONAL_OR_KEYWORD")}
        default = raw.get("default")
        annotation = raw.get("annotation")
        if default is not None:
            entry["default"] = str(default)
        if annotation:
            entry["type"] = str(annotation)
        params.append(entry)
    return params


def _faker_entry(factory: FakerFactory, method: str, locale: str | None) -> dict[str, Any]:
    info = factory.get_method_info(method, locale)
    doc = ""
    parameters = _signature_parameters(info)
    if info and info.get("doc"):
        doc = _first_line(str(info["doc"]))
    if parameters:
        arg_list = ", ".join(p["name"] for p in parameters)
        syntax = "{faker." + method + "(" + arg_list + ")}"
    else:
        syntax = "{faker." + method + "}"
    return {
        "name": f"faker.{method}",
        "kind": "faker",
        "description": doc,
        "syntax": syntax,
        "insert_text": "{faker." + method + "}",
        "parameters": parameters,
    }


def _top_level_faker_entries(factory: FakerFactory, locale: str | None) -> list[dict[str, Any]]:
    fake = factory.get(locale)
    methods: list[dict[str, Any]] = []

    # Backward-compatible shortcuts documented explicitly.
    methods.append(
        {
            "name": "faker.custom",
            "kind": "faker",
            "description": (
                "Resolve a custom provider field by name (e.g. faker.custom(field=otp_code))."
            ),
            "syntax": "{faker.custom(field=...)}",
            "insert_text": '{faker.custom(field="...")}',
            "parameters": [{"name": "field"}, {"name": "locale"}],
        }
    )
    methods.append(
        {
            "name": "faker.profile",
            "kind": "faker",
            "description": (
                "Return one field of a full fake profile (job, name, residence, username, ...)."
            ),
            "syntax": "{faker.profile(field=job)}",
            "insert_text": "{faker.profile(field=job)}",
            "parameters": [
                {"name": "field"},
                {"name": "locale"},
            ],
        }
    )

    seen = {"custom", "profile"}
    for name in dir(fake):
        if name.startswith("_") or name in _FAKER_EXCLUDED or name in seen:
            continue
        try:
            attr = getattr(fake, name, None)
        except Exception:  # noqa: BLE001 - a single bad attribute never kills the catalog
            continue
        if not callable(attr):
            continue
        seen.add(name)
        try:
            methods.append(_faker_entry(factory, name, locale))
        except Exception:  # noqa: BLE001 - introspection failures are non-fatal
            continue
    return methods


def _provider_entries(factory: FakerFactory, locale: str | None) -> list[dict[str, Any]]:
    fake = factory.get(locale)
    families: dict[tuple[str, bool], dict[str, Any]] = {}
    for provider in getattr(fake, "providers", []):
        module = getattr(type(provider), "__module__", "")
        # Modules look like ``faker.providers.<family>`` or, for localized
        # providers, ``faker.providers.<family>.<locale>``.
        parts = module.split(".")
        builtin = len(parts) >= 3 and parts[:2] == ["faker", "providers"]
        family = parts[2] if builtin else type(provider).__name__
        key = (family, builtin)
        bucket = families.setdefault(key, {"family": family, "builtin": builtin, "methods": []})
        for name in sorted(dir(provider)):
            if name.startswith("_"):
                continue
            try:
                attr = getattr(provider, name, None)
            except Exception:  # noqa: BLE001 - defensive per-provider
                continue
            if not callable(attr):
                continue
            path = f"faker.providers.{family}.{name}" if builtin else None
            info = None
            try:
                info = inspect.signature(attr) if callable(attr) else None
                params: list[dict[str, str]] = []
                if info is not None:
                    for pname, param in info.parameters.items():
                        if pname in ("self", "cls"):
                            continue
                        entry = {"name": pname, "kind": param.kind.name}
                        if param.default is not inspect.Parameter.empty:
                            entry["default"] = repr(param.default)
                        if param.annotation is not inspect.Parameter.empty:
                            entry["type"] = str(param.annotation)
                        params.append(entry)
            except (ValueError, TypeError):  # pragma: no cover - C extensions etc.
                params = []
            doc = _first_line(attr.__doc__ or "")
            method_entry: dict[str, Any] = {
                "name": name,
                "kind": "faker.provider",
                "description": doc,
                "syntax": ("{faker." + (path or f"{family}.{name}") + "}"),
                "insert_text": ("{faker." + (path or f"{family}.{name}") + "}"),
                "parameters": params,
            }
            if path:
                method_entry["path"] = path
            bucket["methods"].append(method_entry)
    ordered = sorted(families.values(), key=lambda item: (not item["builtin"], item["family"]))
    return ordered


def build_catalog(locale: str | None = None) -> dict[str, Any]:
    """Build (or fetch from cache) the full placeholder catalog."""
    cached = _CATALOG_CACHE.get(locale)
    if cached is not None:
        return cached

    registry = PlaceholderRegistry()
    from blaze_hammer.templating.builtins import register_builtins

    register_builtins(registry)

    probe_rng = __import__("random").Random(0)
    factory = FakerFactory(probe_rng, default_locale=locale)
    try:
        effective = factory.get(locale).locales[0] if locale else None
    except Exception:  # noqa: BLE001 - unknown locale: report the raw value
        effective = locale

    import faker as faker_module

    catalog: dict[str, Any] = {
        "version": CATALOG_VERSION,
        "faker_version": str(faker_module.VERSION),
        "locale": effective,
        "builtins": _builtin_entries(registry),
        "faker": _top_level_faker_entries(factory, locale),
        "providers": _provider_entries(factory, locale),
    }
    _CATALOG_CACHE[locale] = catalog
    return catalog


def get_catalog_event(locale: str | None = None) -> dict[str, Any]:
    """WS frame payload: ``{"type": "placeholder.catalog", ...}`` (cached)."""
    event_key = ("event", locale)
    cached = _CATALOG_CACHE.get(event_key)
    if cached is not None:
        return cached
    event = {"type": "placeholder.catalog", **build_catalog(locale)}
    _CATALOG_CACHE[event_key] = event
    return event


def invalidate_catalog_cache() -> None:
    """Drop all cached catalogs (call after locale/provider changes)."""
    _CATALOG_CACHE.clear()
