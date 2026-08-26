"""Typed request/response models for the Web GUI API (no internals leak)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class LoginResponse(BaseModel):
    ok: bool
    username: str | None = None


class MeResponse(BaseModel):
    username: str


class RunSettingsPayload(BaseModel):
    """Per-run overrides; everything optional, layered over project YAML."""

    target: str | None = Field(None, max_length=2048)
    method: str | None = None
    requests: int | None = Field(None, ge=1, le=10_000_000)
    concurrency: int | None = Field(None, ge=1, le=10_000)
    delay: float | None = Field(None, ge=0, le=3600)
    rate: float | None = Field(None, gt=0, le=1_000_000)
    timeout: float | None = Field(None, gt=0, le=3600)
    retries: int | None = Field(None, ge=0, le=10)
    seed: int | None = Field(None, ge=-(2**63), le=2**63 - 1)
    faker_locale: str | None = Field(None, max_length=16)
    post_type: str | None = None
    profile: str | None = Field(None, max_length=256)

    def to_overrides(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True, exclude_unset=True)
        if "post_type" in data:
            data["post_type"] = str(data["post_type"]).lower()
        if "method" in data:
            data["method"] = str(data["method"]).upper()
        if "retries" in data:
            # Mirror the CLI: RunConfig.retries is a nested RetryPolicy.
            data["retries"] = {"max_retries": data["retries"]}
        return data


class RunStartRequest(RunSettingsPayload):
    headers_text: str | None = Field(None, max_length=1_000_000)
    payload_text: str | None = Field(None, max_length=4_000_000)


class PreviewRequest(RunStartRequest):
    count: int = Field(3, ge=1, le=20)


class PreviewPlan(BaseModel):
    index: int
    url: str
    method: str
    headers: dict[str, Any] | None = None
    body: dict[str, Any] | None = None


class PreviewResponse(BaseModel):
    plans: list[PreviewPlan]
    sensitive_names: tuple[str, ...] = ()


class ConfigView(BaseModel):
    """Resolved run configuration (safe subset for display)."""

    target: str
    method: str
    requests: int
    concurrency: int
    delay: float
    timeout: float
    retries: int
    rate: float | None
    seed: int | None
    faker_locale: str | None
    post_type: str
    payload_file: str | None
    headers_file: str | None
    web_enabled: bool
    auth_enabled: bool
    #: SHA-256 of blazehammer.yaml; pass back on POST /config/save.
    config_revision: str | None = None


class WebSaveSection(BaseModel):
    """Nested ``web:`` patch — omitted sub-fields stay untouched."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    host: str | None = Field(None, min_length=1, max_length=255)
    port: int | None = Field(None, ge=0, le=65535)


class ResponseLoggingSaveSection(BaseModel):
    """Nested ``response_logging:`` patch (subset editable from the UI)."""

    model_config = ConfigDict(extra="forbid")

    mode: str | None = Field(None, pattern=r"^(none|errors|all)$")
    max_body_bytes: int | None = Field(None, ge=64, le=1_000_000)
    max_headers: int | None = Field(None, ge=0, le=100)


class SaveConfigRequest(BaseModel):
    """PATCH-style configuration update.

    Only fields the client explicitly set are applied (track via
    ``model_fields_set``); omitted keys are never written. ``null`` is a real
    value where the schema allows one (e.g. ``rate``). Unknown/custom YAML
    keys elsewhere in the file always survive.
    """

    model_config = ConfigDict(extra="forbid")

    config_revision: str | None = Field(None, min_length=1, max_length=128)
    target: str | None = Field(None, min_length=1, max_length=2048)
    method: str | None = Field(None, pattern=r"^(GET|POST)$")
    requests: int | None = Field(None, ge=1, le=10_000_000)
    concurrency: int | None = Field(None, ge=1, le=10_000)
    delay: float | None = Field(None, ge=0, le=3600)
    rate: float | None = Field(None, gt=0, le=1_000_000)
    timeout: float | None = Field(None, gt=0, le=3600)
    retries: int | None = Field(None, ge=0, le=10)
    seed: int | None = Field(None, ge=-(2**63), le=2**63 - 1)
    faker_locale: str | None = Field(None, max_length=16)
    post_type: str | None = Field(None, pattern=r"^(json|form)$")

    @field_validator("method", mode="before")
    @classmethod
    def _upper_method(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("post_type", mode="before")
    @classmethod
    def _lower_post_type(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value

    web: WebSaveSection | None = None
    response_logging: ResponseLoggingSaveSection | None = None


class ConfigSaveResponse(BaseModel):
    ok: bool = True
    changed: list[str]
    config_revision: str


class ProfileSummary(BaseModel):
    name: str
    path: str


class ProfileDetail(BaseModel):
    name: str
    path: str
    data: dict[str, Any]


class RunSummary(BaseModel):
    run_id: str
    status: str
    target: str
    method: str
    requested: int
    completed: int = 0
    success: int = 0
    failed: int = 0
    error: str | None = None
    #: Aggregates (additive; None while nothing completed yet).
    average_response_time_ms: float | None = None
    min_response_time_ms: float | None = None
    max_response_time_ms: float | None = None
    status_codes: dict[str, int] = Field(default_factory=dict)


class RunListResponse(BaseModel):
    runs: list[RunSummary]


class ResponseSnapshot(BaseModel):
    """Per-request response record served by ``GET /runs/{id}/log``."""

    request_index: int
    status_code: int | None = None
    response_time_ms: float | None = None
    content_type: str | None = None
    body_size: int = 0
    response_body_excerpt: str | None = None
    response_body_truncated: bool = False
    headers: dict[str, str] = Field(default_factory=dict)
    error: str | None = None

    #: Richer context retained alongside the snapshot.
    ok: bool = True
    attempts: int = 1
    timestamp_ms: int | None = None
    error_category: str | None = None
    request_headers: dict[str, Any] | None = None
    request_body: dict[str, Any] | None = None


class OkResponse(BaseModel):
    ok: bool = True


class ValidationIssue(BaseModel):
    location: str | None = None
    token: str | None = None
    problem: str | None = None
    message: str | None = None
    suggestions: str | None = None


class ValidationResponse(BaseModel):
    ok: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    errors: list[ValidationIssue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Placeholder catalog
# ---------------------------------------------------------------------------


class PlaceholderParameter(BaseModel):
    name: str
    kind: str | None = None
    type: str | None = None
    default: str | None = None


class Placeholder(BaseModel):
    """One completable placeholder (built-in, faker, or faker provider)."""

    name: str
    kind: str
    description: str = ""
    syntax: str = ""
    insert_text: str
    returns: str | None = None
    example: str | None = None
    parameters: list[PlaceholderParameter] = Field(default_factory=list)
    path: str | None = None


class ProviderInfo(BaseModel):
    """One Faker provider family and its callable methods."""

    family: str
    builtin: bool = True
    methods: list[Placeholder] = Field(default_factory=list)


class PlaceholderCatalog(BaseModel):
    version: int
    faker_version: str
    locale: str | None
    builtins: list[Placeholder]
    faker: list[Placeholder]
    providers: list[ProviderInfo]


# ------------------------------------------------------------- templates --


class TemplatesResponse(BaseModel):
    """GET /config/templates — editor text, project-relative paths, revisions."""

    payload_text: str | None = None
    headers_text: str | None = None
    #: Friendly aliases matching the documented contract.
    payload: str | None = None
    headers: str | None = None
    payload_revision: str | None = None
    headers_revision: str | None = None
    payload_file: str | None = None
    headers_file: str | None = None


class SaveTemplatesRequest(BaseModel):
    """POST /config/templates/save — both fields optional and independent.

    Omitted fields are never written. ``*_revision`` enables optimistic
    concurrency: when supplied it must match the file's current revision or
    the save is rejected with 409 TEMPLATE_CONFLICT.
    """

    payload: str | None = Field(None, max_length=4_000_000)
    headers: str | None = Field(None, max_length=1_000_000)
    payload_revision: str | None = Field(None, min_length=1, max_length=128)
    headers_revision: str | None = Field(None, min_length=1, max_length=128)


class SaveTemplatesResponse(BaseModel):
    ok: bool = True
    saved: list[str]
    payload_revision: str | None = None
    headers_revision: str | None = None
