"""Schemas for AirlockRequest."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from trevor.models.request import AirlockDirection, AirlockRequestStatus


class RequestCreate(BaseModel):
    project_id: uuid.UUID
    direction: AirlockDirection
    title: str
    description: str = ""


class RequestRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    direction: AirlockDirection
    status: AirlockRequestStatus
    title: str
    description: str
    submitted_by: uuid.UUID
    submitted_at: datetime | None
    updated_at: datetime
    closed_at: datetime | None

    model_config = {"from_attributes": True}


class OutputObjectRead(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID
    logical_object_id: uuid.UUID
    version: int
    replaces_id: uuid.UUID | None
    filename: str
    output_type: str
    statbarn: str
    storage_key: str
    checksum_sha256: str
    size_bytes: int
    state: str
    uploaded_at: datetime
    uploaded_by: uuid.UUID
    upload_url_generated_at: datetime | None

    model_config = {"from_attributes": True}


class RequestReadWithObjects(RequestRead):
    objects: list[OutputObjectRead] = []


class OutputObjectMetadataUpdate(BaseModel):
    title: str = ""
    description: str = ""
    researcher_justification: str = ""
    suppression_notes: str = ""
    tags: dict[str, Any] = {}


class OutputObjectMetadataRead(BaseModel):
    logical_object_id: uuid.UUID
    title: str
    description: str
    researcher_justification: str
    suppression_notes: str
    checker_feedback: list[Any]
    tags: dict[str, Any]
    updated_at: datetime

    model_config = {"from_attributes": True}


class AuditEventRead(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID | None
    actor_id: str
    event_type: str
    payload: dict[str, Any]
    timestamp: datetime

    model_config = {"from_attributes": True}
