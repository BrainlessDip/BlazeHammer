"""Request planning — the single template-to-request pipeline.

Preview, dry-run and real execution all generate requests through
:meth:`RequestPlanner.next_plan`; there is no separate preview code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from blaze_hammer.security.env import ENV_PATTERN
from blaze_hammer.templating.resolver import TemplateResolver

if TYPE_CHECKING:
    from blaze_hammer.config.models import RunConfig
    from blaze_hammer.files.attachments import AttachmentSet


@dataclass(frozen=True)
class RequestPlan:
    """One fully resolved request, ready for the HTTP client."""

    index: int
    url: str
    method: str
    headers: dict[str, str] | None
    #: JSON-serialisable body (json/post_type=json).
    json_body: dict | list | None
    #: Form-encoded body (post_type=form).
    form_data: dict | None
    #: Raw text body (post_type=raw/xml/html).
    raw_body: str | None
    #: Binary body bytes (post_type=binary).
    body_bytes: bytes | None
    #: httpx content= kwarg value (for raw/binary passthrough).
    content: bytes | None
    post_type: str
    #: Default Content-Type to inject when user hasn't set one.
    default_content_type: str | None

    @property
    def body_preview(self) -> Any:
        """Structured body representation for logging/samples."""
        if self.json_body is not None:
            return self.json_body
        if self.form_data is not None:
            return self.form_data
        if self.raw_body is not None:
            return self.raw_body
        if self.body_bytes is not None:
            return f"[binary {len(self.body_bytes)} bytes]"
        return None


@dataclass
class RequestTemplates:
    """Templates loaded from disk, before placeholder resolution."""

    headers: dict | None = None
    payload: dict | None = None
    sensitive_names: tuple[str, ...] = field(default=())


class RequestPlanner:
    """Resolves templates into concrete request plans, sequentially.

    Generation happens in scheduler order (single coroutine), which is what
    makes ``--seed`` reproducible regardless of network timing.
    """

    def __init__(
        self,
        cfg: RunConfig,
        resolver: TemplateResolver,
        templates: RequestTemplates,
        attachments: AttachmentSet | None = None,
    ) -> None:
        self._cfg = cfg
        self._resolver = resolver
        self._templates = templates
        self._attachments = attachments
        self._sensitive_extra = _collect_sensitive_names(templates)

    @property
    def sensitive_names(self) -> tuple[str, ...]:
        """Header/payload key names to redact (sensitive names + env-derived)."""
        base = tuple(self._cfg.sensitive_keys)
        return (*base, *self._templates.sensitive_names, *self._sensitive_extra)

    def next_plan(self, index: int) -> RequestPlan:
        from blaze_hammer.config.models import PostType

        headers = (
            self._resolver.resolve_obj(self._templates.headers)
            if self._templates.headers is not None and not self._cfg.disable_headers
            else None
        )
        payload = (
            self._resolver.resolve_obj(self._templates.payload)
            if self._templates.payload is not None
            else None
        )
        method_enum = self._cfg.method
        post_type = self._cfg.post_type
        has_body = method_enum.supports_body and post_type != PostType.NONE

        json_body: dict | list | None = None
        form_data: dict | None = None
        raw_body: str | None = None
        body_bytes: bytes | None = None
        content: bytes | None = None

        if has_body and payload is not None:
            if post_type == PostType.JSON:
                json_body = payload if isinstance(payload, (dict, list)) else None
            elif post_type == PostType.FORM:
                form_data = payload if isinstance(payload, dict) else None
            elif post_type in (PostType.RAW, PostType.XML, PostType.HTML):
                # Text body types: payload may be a dict (serialise to string)
                # or already a string.
                if isinstance(payload, str):
                    raw_body = payload
                elif isinstance(payload, (dict, list)):
                    import json as _json

                    raw_body = _json.dumps(payload, ensure_ascii=False)
                else:
                    raw_body = str(payload) if payload is not None else None
            elif post_type == PostType.MULTIPART:
                # Multipart: form dict + optional file attachments.
                form_data = payload if isinstance(payload, dict) else None
            elif post_type == PostType.BINARY:
                # Binary: payload is raw text; encode to bytes.
                if isinstance(payload, str):
                    body_bytes = payload.encode("utf-8", errors="replace")
                    content = body_bytes
                elif isinstance(payload, (dict, list)):
                    import json as _json

                    body_bytes = _json.dumps(
                        payload, ensure_ascii=False,
                    ).encode("utf-8")
                    content = body_bytes

        # File-payload (attachments) overrides the body for multipart.
        if post_type == PostType.MULTIPART and self._cfg.file_payload:
            form_data = form_data or {}
            content = None  # httpx files= kwarg is used instead.

        return RequestPlan(
            index=index,
            url=self._cfg.target,
            method=self._cfg.method.value,
            headers=headers or None,
            json_body=json_body,
            form_data=form_data,
            raw_body=raw_body,
            body_bytes=body_bytes,
            content=content,
            post_type=post_type.value,
            default_content_type=post_type.default_content_type,
        )

    def preview_plans(self, count: int) -> list[RequestPlan]:
        return [self.next_plan(i) for i in range(count)]

    def httpx_files(self) -> dict | None:
        if not self._cfg.file_payload or self._attachments is None:
            return None
        return self._attachments.as_httpx_files()


def _collect_sensitive_names(templates: RequestTemplates) -> tuple[str, ...]:
    """Names whose values reference ${ENV} vars — treat as secrets."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and ENV_PATTERN.search(value):
                    found.append(str(key))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for template in (templates.headers, templates.payload):
        walk(template)
    return tuple(dict.fromkeys(found))
