"""api / schemas."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class StartHarnessRequest(BaseModel):
    session_id: str
    goal: str
    max_turns: Optional[int] = None
    primary_model: Optional[str] = None
    reviewer_model: Optional[str] = None
    workspace_path: Optional[str] = None


class CommandRequest(BaseModel):
    session_id: Optional[str] = None
    command: str
    args: str = ""


class CreateWorkspaceRequest(BaseModel):
    name: str


class ApproveRequest(BaseModel):
    allow: bool
