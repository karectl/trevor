"""ProjectMembership API schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from trevor.models.project import ProjectRole


class MembershipCreate(BaseModel):
    user_id: uuid.UUID
    project_id: uuid.UUID
    role: ProjectRole


class MembershipRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID
    role: ProjectRole
    assigned_by: uuid.UUID | None = None
    assigned_at: datetime
