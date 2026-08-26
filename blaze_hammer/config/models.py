"""Validated run configuration (Pydantic v2).

One :class:`RunConfig` is built per invocation — CLI > environment >
profile > defaults — and passed through the whole application. Functions
never accept a dozen loose parameters.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_REQUESTS = 10_000_000
MAX_CONCURRENCY = 10_000


class Method(StrEnum):
    GET = "GET"
    POST = "POST"


class ResponseLoggingMode(StrEnum):
    NONE = "none"
    ERRORS = "errors"
    ALL = "all"


class ResponseLoggingOptions(BaseModel):
    """Per-run response snapshot policy (``response_logging:`` YAML section).

    ``mode='errors'`` keeps excerpts for failed/non-success responses only;
    bodies are never read at all under ``none``. Byte caps bound memory and
    storage regardless of mode.
    """

    model_config = ConfigDict(extra="forbid")

    mode: ResponseLoggingMode = ResponseLoggingMode.ERRORS
    max_body_bytes: int = Field(4096, ge=64, le=1_000_000)
    max_headers: int = Field(20, ge=0, le=100)
    #: Overrides the built-in response-header allowlist when non-empty.
    allow_headers: tuple[str, ...] = ()
    #: JSON keys redacted inside excerpts (case-insensitive); off by default.
    redact_keys: tuple[str, ...] = ()


class PostType(StrEnum):
    JSON = "json"
    FORM = "form"


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_retries: int = Field(0, ge=0, le=10)
    backoff_base: float = Field(0.5, gt=0, le=60)
    backoff_cap: float = Field(8.0, gt=0, le=300)
    retry_statuses: tuple[int, ...] = (502, 503, 504)
    retry_on_timeout: bool = True


class PreviewOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool = False
    preview_count: int | None = Field(None, ge=1, le=100)


class OutputOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simple: bool = False
    print_payload: bool = False
    print_headers: bool = False
    print_response: bool = False
    status_filter: frozenset[int] | None = None
    failed_only: bool = False
    success_only: bool = False
    max_response_size: int = Field(2000, ge=1, le=10_000_000)
    save_responses_dir: Path | None = None
    export_path: Path | None = None
    log_level: str = "WARNING"
    log_file: Path | None = None
    debug: bool = False

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        return value.upper()


class WebAuth(BaseModel):
    """Web GUI credentials. ``password`` is plaintext-in-config-only; it is
    hashed in memory at startup and never logged or written back."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    username: str | None = None
    password: str | None = None
    password_hash: str | None = None


class WebCors(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    #: Explicit browser origins permitted with credentials. Development
    #: defaults cover local React/Vite servers; production must set these.
    allow_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )


class WebOptions(BaseModel):
    """Web GUI server settings (``web:`` section of blazehammer.yaml)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    host: str = "127.0.0.1"
    #: 0 picks a free ephemeral port at startup.
    port: int = Field(8080, ge=0, le=65535)
    auth: WebAuth = Field(default_factory=WebAuth)
    cors: WebCors = Field(default_factory=WebCors)


class RunConfig(BaseModel):
    """Complete, validated description of one load test."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    target: str
    method: Method = Method.GET
    payload_file: Path | None = None
    headers_file: Path | None = None
    disable_headers: bool = False
    post_type: PostType = PostType.JSON
    file_payload: bool = False
    requests: int = Field(100, ge=1, le=MAX_REQUESTS)
    concurrency: int = Field(100, ge=1, le=MAX_CONCURRENCY)
    delay: float = Field(0.0, ge=0.0, le=3600.0)
    rate: float | None = Field(None, gt=0.0, le=1_000_000.0)
    timeout: float = Field(30.0, gt=0.0, le=3600.0)
    seed: int | None = Field(None, ge=-(2**63), le=2**63 - 1)
    faker_locale: str | None = Field(None, pattern=r"^[a-z]{2}(_[A-Z]{2})?$")
    #: Set internally by the Web GUI when payload/header JSON arrives inline
    #: (editors) instead of through files; relaxes file-presence checks.
    inline_templates: bool = False
    profile: str | None = None
    assume_yes: bool = False
    sensitive_keys: tuple[str, ...] = ()
    retries: RetryPolicy = Field(default_factory=RetryPolicy)
    preview: PreviewOptions = Field(default_factory=PreviewOptions)
    output: OutputOptions = Field(default_factory=OutputOptions)
    response_logging: ResponseLoggingOptions = Field(default_factory=ResponseLoggingOptions)
    web: WebOptions = Field(default_factory=WebOptions)

    @property
    def needs_bodies(self) -> bool:
        """Whether response bodies must be captured at all."""
        out = self.output
        return bool(
            out.print_response
            or out.save_responses_dir
            or self.response_logging.mode != ResponseLoggingMode.NONE
        )

    @property
    def prints_per_request(self) -> bool:
        return bool(
            self.output.print_payload or self.output.print_headers or self.output.print_response
        )
