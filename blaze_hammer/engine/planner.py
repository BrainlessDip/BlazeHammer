"""Request planning — the single template-to-request pipeline.

Preview, dry-run and real execution all generate requests through
:meth:`RequestPlanner.next_plan`; there is no separate preview code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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
    json_body: dict | None
    form_data: dict | None
    post_type: str

    @property
    def body_preview(self) -> dict | None:
        return self.json_body if self.json_body is not None else self.form_data


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
        if self._cfg.method.value == "POST":
            if self._cfg.post_type.value == "json":
                json_body, form_data = payload or {}, None
            else:
                json_body, form_data = None, payload or {}
        else:
            json_body = form_data = None
        return RequestPlan(
            index=index,
            url=self._cfg.target,
            method=self._cfg.method.value,
            headers=headers or None,
            json_body=json_body,
            form_data=form_data,
            post_type=self._cfg.post_type.value,
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
