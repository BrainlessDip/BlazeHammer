"""Typed request/response models for the Web GUI API (no internals leak)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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


class SaveConfigRequest(BaseModel):
    confirm: bool = False
    target: str | None = None
    method: str | None = None
    requests: int | None = Field(None, ge=1, le=10_000_000)
    concurrency: int | None = Field(None, ge=1, le=10_000)
    delay: float | None = Field(None, ge=0, le=3600)
    timeout: float | None = Field(None, gt=0, le=3600)
    faker_locale: str | None = Field(None, max_length=16)


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


class RunListResponse(BaseModel):
    runs: list[RunSummary]


class OkResponse(BaseModel):
    ok: bool = True
