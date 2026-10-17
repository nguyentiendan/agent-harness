"""HTTP routes for commands."""

from fastapi import APIRouter

from app.services.commands import run_command

router = APIRouter()

router.post('/api/command')(run_command)
