from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class JobPayload(BaseModel):
    """Envelope for single-job endpoints. Accepts either a nested 'request' key
    or a flat payload where source/destination are top-level keys."""
    model_config = ConfigDict(extra="allow")
    request: dict | None = None
    resume_job_id: str | None = None


class ResumeJobPayload(BaseModel):
    """Envelope for /jobs/resume. job_id is required."""
    model_config = ConfigDict(extra="allow")
    job_id: str
    request: dict | None = None


class BatchPayload(BaseModel):
    """Base envelope for batch endpoints."""
    csv_content: str
    base_request: dict
    batch_name: str | None = None
    allow_partial: bool = False


class BatchPreflightPayload(BatchPayload):
    """Batch preflight adds a row limit."""
    limit: int = Field(default=20, ge=1, le=200)
