"""HTTP routes for config."""

from fastapi import APIRouter

from app.services.workspaces import get_config

router = APIRouter()

router.get('/api/config')(get_config)
