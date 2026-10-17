"""HTTP routes for sessions."""

from fastapi import APIRouter

from app.services.sessions import delete_session, get_session, list_sessions

router = APIRouter()

router.get('/api/sessions')(list_sessions)
router.get('/api/sessions/{session_id}')(get_session)
router.delete('/api/sessions/{session_id}')(delete_session)
