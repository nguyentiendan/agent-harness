"""HTTP routes for runs."""

from fastapi import APIRouter

from app.services.runs import get_status, start_harness, stop_harness, stream_progress

router = APIRouter()

router.post('/api/harness/start')(start_harness)
router.post('/api/harness/stop/{session_id}')(stop_harness)
router.get('/api/harness/status/{session_id}')(get_status)
router.get('/api/harness/stream/{session_id}')(stream_progress)
