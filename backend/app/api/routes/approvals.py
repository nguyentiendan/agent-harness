"""HTTP routes for approvals."""

from fastapi import APIRouter

from app.services.approvals import list_approvals, resolve_approval

router = APIRouter()

router.get('/api/harness/approvals/{session_id}')(list_approvals)
router.post('/api/harness/approve')(resolve_approval)
