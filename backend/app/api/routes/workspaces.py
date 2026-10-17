"""HTTP routes for workspaces."""

from fastapi import APIRouter

from app.services.files import get_workspace_files, list_workspace, read_workspace_file
from app.services.workspaces import create_workspace, list_workspaces

router = APIRouter()

router.get('/api/workspaces')(list_workspaces)
router.post('/api/workspaces')(create_workspace)
router.get('/api/harness/files/{session_id}')(get_workspace_files)
router.get('/api/harness/list/{session_id}')(list_workspace)
router.get('/api/harness/read/{session_id}')(read_workspace_file)
