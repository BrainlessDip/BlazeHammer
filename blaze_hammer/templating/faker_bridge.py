"""Faker integration: dynamic resolver, seeded factory, custom providers.

One interface to the entire installed Faker API.  Resolves:

  faker.<method>                     – dynamic attribute lookup on the Faker instance
  faker.providers.<family>.<method>  – explicit provider path
  faker.profile(field=...)           – backward-compatible profile shortcut
  faker.custom(field=...)            – backward-compatible custom shortcut

Native return types are preserved when a faker token occupies an *entire*
template value (``{faker.pybool}`` → ``true``).  Embedded usage always
produces strings (``"user-{faker.pybool}"`` → ``"user-true"``).
"""

from __future__ import annotations

import ast
import difflib
import importlib
import inspect
import random
import re
from typing import TYPE_CHECKING, Any

from faker import Faker
from faker.providers import BaseProvider

from blaze_hammer.errors import TemplateResolutionError
from blaze_hammer.templating.registry import ArgsDict, ResolveContext

if TYPE_CHECKING:
    from collections.abc import Sequence

# ---------------------------------------------------------------------------
# Legacy-compatible exports (used by resolver.py, validation.py, tests)
# ---------------------------------------------------------------------------

FAKER_PATTERN = r"faker\.((providers\.[\w\.]+)|[\w_]+)(\((.*?)\))?"
PROVIDER_PATTERN = r"providers\.([\w\.]+)"

_FAKER_RE = re.compile(FAKER_PATTERN)
_PROVIDER_RE = re.compile(PROVIDER_PATTERN)
# Legacy key=value regex for backward-compat with simple arg strings.
_ARGS_RE = re.compile(r"(\w+)=([^,{}()]+)")

# ---------------------------------------------------------------------------
# Security: blocked attribute names
# ---------------------------------------------------------------------------

_BLOCKED_ATTRS = frozenset(
    {
        "__class__",
        "__subclasses__",
        "__bases__",
        "__mro__",
        "__import__",
        "__builtins__",
        "__globals__",
        "__code__",
        "__dict__",
        "__getattr__",
        "__setattr__",
        "__delattr__",
        "__loader__",
        "__spec__",
        "__file__",
        "__name__",
        "__package__",
        "__qualname__",
    }
)

_BLOCKED_BUILTIN_MODULES = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "builtins",
        "importlib",
        "code",
        "compile",
        "exec",
        "eval",
        "breakpoint",
        "exit",
        "quit",
        "__import__",
        "pty",
        "signal",
        "ctypes",
        "socket",
        "shutil",
        "pathlib",
        "tempfile",
        "io",
        "codecs",
    }
)

# ---------------------------------------------------------------------------
# Custom provider discovery
# ---------------------------------------------------------------------------


def discover_custom_providers(module_name: str) -> list[type[BaseProvider]]:
    """Return every ``BaseProvider`` subclass defined in *module_name*."""
    module = importlib.import_module(module_name)
    return [
        obj
        for obj in vars(module).values()
        if inspect.isclass(obj)
        and issubclass(obj, BaseProvider)
        and obj is not BaseProvider
        and obj.__module__ == module_name
    ]


# ---------------------------------------------------------------------------
# Argument parser — positional + keyword, all Python literal types
# ---------------------------------------------------------------------------


def _coerce_value(raw: str) -> Any:
    """Convert a raw argument string to a Python value.

    Tries ``ast.literal_eval`` first (handles int, float, bool, None, str,
    list, tuple, dict).  Falls back to plain string on failure.
    """
    stripped = raw.strip()
    if not stripped:
        return ""
    try:
        return ast.literal_eval(stripped)
    except (ValueError, SyntaxError):
        return stripped


def parse_faker_args(content: str) -> tuple[list[Any], dict[str, Any]]:
    """Parse positional and keyword arguments from a faker token.

    Examples::

        faker.name                        → ([], {})
        faker.random_int(min=1,max=100)   → ([], {"min": 1, "max": 100})
        faker.pystr(10)                   → ([10], {})
        faker.random_int(1, 100)          → ([1, 100], {})
        faker.date(pattern="%Y-%m-%d")    → ([], {"pattern": "%Y-%m-%d"})

    Supports: int, float, bool, None, str (single/double quoted),
    list, tuple, dict, and bare identifiers.
    """
    paren_start = content.find("(")
    if paren_start == -1 or not content.rstrip().endswith(")"):
        return [], {}

    arg_str = content[paren_start + 1 : -1].strip()
    if not arg_str:
        return [], {}

    positional: list[Any] = []
    keywords: dict[str, Any] = {}

    for token in _tokenize_args(arg_str):
        if token == ",":
            continue
        eq_pos = _find_unquoted_eq(token)
        if eq_pos is not None:
            key = token[:eq_pos].strip()
            val = token[eq_pos + 1 :].strip()
            if key and val:
                keywords[key] = _coerce_value(val)
        elif token:
            positional.append(_coerce_value(token))

    return positional, keywords


def _find_unquoted_eq(s: str) -> int | None:
    """Find the first ``=`` not inside quotes."""
    in_single = False
    in_double = False
    for i, ch in enumerate(s):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "=" and not in_single and not in_double:
            return i
    return None


def _tokenize_args(arg_str: str) -> list[str]:
    """Split an argument string on commas, respecting quotes and parens."""
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    in_single = False
    in_double = False

    for ch in arg_str:
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif ch in ("(", "[", "{") and not in_single and not in_double:
            depth += 1
            current.append(ch)
        elif ch in (")", "]", "}") and not in_single and not in_double:
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0 and not in_single and not in_double:
            tokens.append("".join(current).strip())
            current = []
        else:
            current.append(ch)

    last = "".join(current).strip()
    if last:
        tokens.append(last)
    return tokens


def parse_faker_args_legacy(content: str) -> ArgsDict:
    """Parse kwargs as ``{key: raw_string}`` — backward-compatible with old regex."""
    return dict(_ARGS_RE.findall(content))


# ---------------------------------------------------------------------------
# Security: attribute validation
# ---------------------------------------------------------------------------


def _validate_attr_name(name: str) -> None:
    """Reject unsafe attribute names."""
    if name in _BLOCKED_ATTRS:
        raise TemplateResolutionError(
            f"Access to '{name}' is not allowed for security reasons",
            token=f"faker.{name}",
        )
    if name.startswith("_"):
        raise TemplateResolutionError(
            f"Private attribute '{name}' is not allowed",
            token=f"faker.{name}",
        )
    if name in _BLOCKED_BUILTIN_MODULES:
        raise TemplateResolutionError(
            f"Access to module '{name}' is not allowed for security reasons",
            token=f"faker.{name}",
        )


# ---------------------------------------------------------------------------
# Signature introspection for validation
# ---------------------------------------------------------------------------


def _get_method_signature_info(
    fake: Faker, method_name: str, locale: str | None = None
) -> dict[str, Any] | None:
    """Get signature information for a Faker method.

    Returns a dict with ``name``, ``doc``, ``params`` (list of dicts with
    ``name``, ``kind``, ``default``, ``annotation``), or ``None`` if the
    method cannot be introspected.
    """
    try:
        if not hasattr(fake, method_name):
            return None
        attr = getattr(fake, method_name)
        if not callable(attr):
            return {"name": method_name, "doc": "", "params": [], "returns": type(attr).__name__}
        sig = inspect.signature(attr)
        params = []
        for pname, param in sig.parameters.items():
            if pname in ("self", "cls"):
                continue
            p_info: dict[str, Any] = {"name": pname, "kind": param.kind.name}
            if param.default is not inspect.Parameter.empty:
                p_info["default"] = (
                    repr(param.default) if not isinstance(param.default, str) else param.default
                )
            if param.annotation is not inspect.Parameter.empty:
                p_info["annotation"] = str(param.annotation)
            params.append(p_info)
        doc = (attr.__doc__ or "").strip().split("\n")[0]
        return {"name": method_name, "doc": doc, "params": params}
    except (ValueError, TypeError):
        return None


def _validate_method_args(
    fake: Faker,
    method_name: str,
    positional: list[Any],
    kwargs: dict[str, Any],
    locale: str | None = None,
) -> str | None:
    """Validate arguments against a Faker method's signature.

    Returns an error message string, or ``None`` if valid.
    """
    info = _get_method_signature_info(fake, method_name, locale)
    if info is None:
        return None  # can't introspect; let runtime catch errors

    params = info["params"]
    if not params:
        if positional or kwargs:
            return f"faker.{method_name} takes no arguments"
        return None

    # Check for unexpected keyword arguments
    valid_kw = {p["name"] for p in params}
    for kw in kwargs:
        if kw not in valid_kw:
            close = difflib.get_close_matches(kw, list(valid_kw), n=1, cutoff=0.6)
            hint = f"; did you mean '{close[0]}'?" if close else ""
            return f"Unknown keyword argument '{kw}' for faker.{method_name}{hint}"

    # Check positional count (rough — we don't enforce strict arity since
    # many Faker methods have *args/**kwargs signatures)
    required = [
        p
        for p in params
        if "default" not in p and p["kind"] in ("POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD")
    ]
    if len(positional) > len(params) and not any(p["kind"] == "VAR_POSITIONAL" for p in params):
        return (
            f"faker.{method_name} takes at most {len(params)} positional arguments, "
            f"got {len(positional)}"
        )
    if len(positional) < len(required):
        missing = [p["name"] for p in required[len(positional) :]]
        return f"faker.{method_name} missing required positional argument(s): {', '.join(missing)}"

    return None


# ---------------------------------------------------------------------------
# Suggestion support
# ---------------------------------------------------------------------------


def suggest_faker_fields(bad: str, candidates: Sequence[str]) -> tuple[str, ...]:
    """Return fuzzy-match suggestions for a bad Faker method name."""
    tail = bad.split(".")[-1]
    matches = difflib.get_close_matches(tail, list(candidates), n=3, cutoff=0.5)
    return tuple(matches)


# ---------------------------------------------------------------------------
# FakerFactory — creates and caches seeded Faker instances
# ---------------------------------------------------------------------------


class FakerFactory:
    """Creates (and caches) Faker instances, deterministically seeded.

    Supports a global default locale (set via ``--faker-locale``) and
    per-placeholder ``locale=`` overrides.
    """

    def __init__(
        self,
        rng: random.Random,
        providers_module: str = "blaze_hammer.ext.providers",
        default_locale: str | None = None,
    ) -> None:
        self._rng = rng
        self._providers_module = providers_module
        self._default_locale = default_locale
        self._custom_providers: list[type[BaseProvider]] | None = None
        self._cache: dict[tuple[str | None, ...], Faker] = {}
        self._method_cache: dict[str, dict[str, Any] | None] = {}

    def _providers(self) -> list[type[BaseProvider]]:
        if self._custom_providers is None:
            self._custom_providers = discover_custom_providers(self._providers_module)
        return self._custom_providers

    def get(self, locale: str | None = None) -> Faker:
        """Get or create a Faker instance for the given locale.

        Falls back to the global default locale when *locale* is ``None``.
        """
        effective = locale if locale is not None else self._default_locale
        cache_key = (effective,)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            instance = Faker(effective) if effective else Faker()
        except Exception as exc:
            raise TemplateResolutionError(
                f"Cannot create Faker instance for locale '{effective}'",
                reason=str(exc),
                hint="see https://faker.readthedocs.io/en/stable/locales.html",
            ) from exc
        instance.seed_instance(self._rng.getrandbits(32))
        for provider in self._providers():
            instance.add_provider(provider)
        self._cache[cache_key] = instance
        return instance

    def get_with_overrides(self, locale: str | None = None, **overrides: Any) -> Faker:
        """Get a Faker instance with per-placeholder locale overrides.

        If *locale* is specified, creates an instance for that locale (which
        may differ from the global default).  The instance is seeded from the
        master RNG for deterministic behaviour.
        """
        return self.get(locale)

    @property
    def default_locale(self) -> str | None:
        return self._default_locale

    # -- suggestion support -------------------------------------------------

    def attribute_candidates(self, locale: str | None = None) -> list[str]:
        """All non-private attribute names on the Faker instance."""
        fake = self.get(locale)
        return [name for name in dir(fake) if not name.startswith("_")]

    def provider_path_candidates(self, locale: str | None = None) -> list[str]:
        """Dotted ``providers.<family>.<method>`` names for suggestions."""
        fake = self.get(locale)
        out: list[str] = []
        for provider in getattr(fake, "providers", []):
            module = getattr(type(provider), "__module__", "")
            if not module.startswith("faker.providers"):
                continue
            family = module.split(".")[-1]
            for name in dir(provider):
                if not name.startswith("_"):
                    out.append(f"providers.{family}.{name}")
        return out

    # -- introspection ------------------------------------------------------

    def get_method_info(self, method_name: str, locale: str | None = None) -> dict[str, Any] | None:
        """Get signature/docstring information for a Faker method."""
        cache_key = f"{locale or ''}:{method_name}"
        if cache_key not in self._method_cache:
            fake = self.get(locale)
            self._method_cache[cache_key] = _get_method_signature_info(fake, method_name, locale)
        return self._method_cache[cache_key]

    def list_providers(self, locale: str | None = None) -> dict[str, list[str]]:
        """Group methods by their provider family for the ``faker list`` command."""
        fake = self.get(locale)
        families: dict[str, list[str]] = {}
        for provider in getattr(fake, "providers", []):
            module = getattr(type(provider), "__module__", "")
            if not module.startswith("faker.providers"):
                continue
            family = module.split(".")[-1]
            methods = sorted(
                name
                for name in dir(provider)
                if not name.startswith("_") and callable(getattr(provider, name, None))
            )
            if methods:
                families[family] = methods
        # Add top-level shortcuts
        top_methods = []
        for name in dir(fake):
            if name.startswith("_"):
                continue
            if name in ("seed",):  # deprecated instance method
                continue
            try:
                if callable(getattr(fake, name, None)):
                    top_methods.append(name)
            except (TypeError, AttributeError):
                continue
        top_methods = sorted(
            name
            for name in top_methods
            if not any(name in methods for methods in families.values())
        )
        if top_methods:
            families["(top-level)"] = top_methods[:50]
        return families

    def list_available_locales(self) -> list[str]:
        """Return all locale codes supported by the installed Faker."""
        return sorted(Faker().locales)


# ---------------------------------------------------------------------------
# Token resolver
# ---------------------------------------------------------------------------


def resolve_faker_token(content: str, ctx: ResolveContext) -> Any:
    """Resolve a ``faker.*`` placeholder token.

    Returns the raw result (preserving native types) — the caller
    (``TemplateResolver``) decides whether to coerce to text based on
    whether the token occupies the entire template value.
    """
    positional, kwargs = parse_faker_args(content)
    locale = kwargs.pop("locale", None)
    fake = ctx.faker.get(locale)

    # -- backward-compatible shortcuts -------------------------------------

    if content.startswith("faker.profile"):
        field_name = kwargs.get("field", positional[0] if positional else "job")
        if not isinstance(field_name, str):
            field_name = str(field_name)
        profile = fake.profile()
        if field_name not in profile:
            raise TemplateResolutionError(
                f"Invalid faker.profile field '{field_name}'",
                token=content,
                suggestions=tuple(sorted(profile)),
            )
        return profile[field_name]

    if content.startswith("faker.custom"):
        custom_field = kwargs.get("field", positional[0] if positional else None)
        if isinstance(custom_field, str) and hasattr(fake, custom_field):
            value = getattr(fake, custom_field)
            return value() if callable(value) else value
        suggestions: Sequence[str] = ()
        if isinstance(custom_field, str):
            suggestions = suggest_faker_fields(custom_field, ctx.faker.attribute_candidates())
        raise TemplateResolutionError(
            f"Invalid faker.custom field '{custom_field}'",
            token=content,
            suggestions=tuple(suggestions),
        )

    # -- parse the token path -----------------------------------------------

    match = _FAKER_RE.match(content)
    if not match:
        raise TemplateResolutionError(
            f"Unrecognized faker placeholder '{content}'",
            token=content,
            suggestions=suggest_faker_fields(
                content, ["faker." + c for c in ctx.faker.attribute_candidates()]
            ),
        )

    full_path = match.group(1)
    if not full_path:
        raise TemplateResolutionError(f"Unrecognized faker placeholder '{content}'", token=content)

    # -- explicit provider path: faker.providers.<family>.<method> ----------

    if full_path.startswith("providers."):
        prov_match = _PROVIDER_RE.match(full_path)
        if not prov_match:
            raise TemplateResolutionError(f"Invalid provider path '{full_path}'", token=content)
        parts = prov_match.group(1).split(".")
        if len(parts) < 2:
            raise TemplateResolutionError(
                f"Invalid provider path '{full_path}' (expected providers.<family>.<method>)",
                token=content,
            )
        method_name = parts[-1]
        _validate_attr_name(method_name)

        # Build the provider instance via the Faker instance
        module_path = "faker.providers." + ".".join(parts[:-1])
        try:
            module = importlib.import_module(module_path)
            provider = module.Provider(fake)
            method = getattr(provider, method_name)
        except (ImportError, AttributeError) as exc:
            candidates = ctx.faker.provider_path_candidates(
                locale
            ) + ctx.faker.attribute_candidates(locale)
            raise TemplateResolutionError(
                f"Invalid faker field '{full_path}'",
                token=content,
                suggestions=suggest_faker_fields(full_path, candidates),
                reason=str(exc),
            ) from exc

        # Validate arguments against signature
        arg_err = _validate_method_args(fake, method_name, positional, kwargs, locale)
        if arg_err:
            raise TemplateResolutionError(arg_err, token=content)

        try:
            if callable(method):
                return method(*positional, **kwargs)
            return method
        except TemplateResolutionError:
            raise
        except Exception as exc:
            raise TemplateResolutionError(
                f"Provider error for '{full_path}': {exc}",
                token=content,
                hint="check argument names/types against the Faker docs",
            ) from exc

    # -- dynamic attribute: faker.<method> ----------------------------------

    method_name = full_path
    _validate_attr_name(method_name)

    if not hasattr(fake, method_name):
        candidates = ctx.faker.attribute_candidates(locale) + ctx.faker.provider_path_candidates(
            locale
        )
        raise TemplateResolutionError(
            f"Invalid faker field '{method_name}'",
            token=content,
            suggestions=suggest_faker_fields(method_name, candidates),
            hint=f"Installed Faker version: {__import__('faker').VERSION}",
        )

    attr = getattr(fake, method_name)

    # Validate arguments against signature
    arg_err = _validate_method_args(fake, method_name, positional, kwargs, locale)
    if arg_err:
        raise TemplateResolutionError(arg_err, token=content)

    try:
        if callable(attr):
            return attr(*positional, **kwargs)
        return attr
    except TemplateResolutionError:
        raise
    except Exception as exc:
        raise TemplateResolutionError(
            f"faker.{method_name} failed: {exc}",
            token=content,
            hint="check argument names/types against the Faker docs",
        ) from exc
