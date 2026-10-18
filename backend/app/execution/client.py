"""Server-only authenticated transport; callers supply IDs, never Docker options."""

import os
import httpx


class RunnerUnavailable(RuntimeError):
    pass


def enabled():
    return os.environ.get("SANDBOX_ENABLED", "false").lower() == "true"


class RunnerClient:
    def call(self, method, path, payload=None):
        token = os.environ.get("RUNNER_TOKEN", "")
        if len(token) < 32:
            raise RunnerUnavailable("Sandbox runner is not configured.")
        try:
            with httpx.Client(
                base_url=os.environ.get("RUNNER_URL", "http://sandbox-runner:8080"),
                headers={"Authorization": "Bearer " + token},
                timeout=130 if path.endswith("/exec") else 20,
                trust_env=False,
            ) as client:
                r = client.request(method, path, json=payload)
            if not r.is_success:
                raise RunnerUnavailable(
                    "Sandbox request failed. The run cannot execute outside its sandbox."
                )
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise RunnerUnavailable("Sandbox runner unavailable.") from e

    def ensure(self, ctx):
        return self.call(
            "POST",
            f"/runs/{ctx['run_id']}/ensure",
            {"owner_id": ctx["owner_id"], "scope_id": ctx["scope_id"]},
        )

    def execute(self, rid, payload):
        return self.call("POST", f"/runs/{rid}/exec", payload)

    def destroy(self, rid):
        return self.call("DELETE", f"/runs/{rid}")

    def files(self, owner, sid, op, path=""):
        return self.call(
            "POST",
            "/files",
            {"owner_id": owner, "scope_id": sid, "op": op, "path": path},
        )

    def purge(self, owner, sid):
        return self.call("POST", "/scopes/purge", {"owner_id": owner, "scope_id": sid})
