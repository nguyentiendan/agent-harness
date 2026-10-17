"""services / approvals."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import asyncio
import time

from fastapi import HTTPException

from app.api.schemas import ApproveRequest


class ApprovalBroker:
    def __init__(self):
        self.pending: Dict[str, Dict[str, Any]] = {}
        self._counter = 0

    def create(self, session_id: str, name: str, args: Dict[str, Any], reason: str) -> str:
        self._counter += 1
        aid = f"approval_{self._counter}_{int(time.time() * 1000)}"
        self.pending[aid] = {
            "session_id": session_id, "name": name, "args": args,
            "reason": reason, "future": asyncio.get_running_loop().create_future(),
            "created_at": time.time(),
        }
        return aid

    async def wait(self, approval_id: str) -> bool:
        item = self.pending.get(approval_id)
        if not item:
            return False
        try:
            return await item["future"]
        finally:
            self.pending.pop(approval_id, None)

    def list_pending(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        out = []
        for aid, item in self.pending.items():
            if session_id and item["session_id"] != session_id:
                continue
            out.append({
                "approval_id": aid, "session_id": item["session_id"],
                "name": item["name"], "args": item["args"], "reason": item["reason"],
                "created_at": item["created_at"],
            })
        return out

    def resolve(self, approval_id: str, allow: bool) -> bool:
        item = self.pending.get(approval_id)
        if not item or item["future"].done():
            return False
        item["future"].set_result(bool(allow))
        return True


broker = ApprovalBroker()


async def list_approvals(session_id: str):
    return {"approvals": broker.list_pending(session_id)}


async def resolve_approval(payload: ApproveRequest, approval_id: str):
    if not broker.resolve(approval_id, payload.allow):
        raise HTTPException(status_code=404, detail="Approval not found or already resolved.")
    return {"approval_id": approval_id, "allowed": payload.allow}
