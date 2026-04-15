"""Project API schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from trevor.models.project import ProjectStatus


class ProjectRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    crd_name: str
    display_name: str
    status: ProjectStatus
    synced_at: datetime
