"""Run manually inside backend Docker: fake upstream only; creates/removes temporary gateway routes and keys."""

import os, uuid, httpx, hashlib, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

captured = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        captured.append(
            json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        )
        body = json.dumps(
            {
                "id": "chatcmpl-contract-" + uuid.uuid4().hex,
                "object": "chat.completion",
                "created": 1,
                "model": "vendor/mock:free",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "contract OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 2,
                    "total_tokens": 4,
                    "cost": 0,
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


server = HTTPServer(("0.0.0.0", 18081), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
mid = str(uuid.uuid4())
model = "or-contract-" + uuid.uuid4().hex
raw = None
provider = {
    "allow_fallbacks": False,
    "require_parameters": True,
    "max_price": {"prompt": 0, "completion": 0, "request": 0},
}
with httpx.Client(
    base_url=os.environ["LITELLM_PROXY_URL"],
    headers={"Authorization": "Bearer " + os.environ["LITELLM_MASTER_KEY"]},
    timeout=30,
) as c:
    try:
        r = c.post(
            "/model/new",
            json={
                "model_name": model,
                "litellm_params": {
                    "model": "openrouter/vendor/mock:free",
                    "api_key": "invalid-contract-key",
                    "api_base": "http://backend:18081/v1",
                    "provider": provider,
                    "input_cost_per_token": 0,
                    "output_cost_per_token": 0,
                },
                "model_info": {
                    "id": mid,
                    "input_cost_per_token": 0,
                    "output_cost_per_token": 0,
                },
            },
        )
        r.raise_for_status()
        r = c.post(
            "/key/generate",
            json={"models": [model], "max_budget": None, "duration": "300s"},
        )
        r.raise_for_status()
        raw = r.json()["key"]
        with httpx.Client(
            base_url=os.environ["LITELLM_PROXY_URL"],
            headers={"Authorization": "Bearer " + raw},
            timeout=30,
        ) as k:
            r = k.post(
                "/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "contract"}],
                    "max_tokens": 8,
                },
            )
            print("mock HTTP status", r.status_code, "upstream_calls", len(captured))
            assert r.status_code == 200
            assert captured[0].get("provider") == provider, captured[0].get("provider")
            print("zero price caps + fallback disabled reached upstream: PASS")
            r = k.post(
                "/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "must reject"}],
                    "max_tokens": 1,
                },
            )
            assert r.status_code == 403
            print("Paid model denied: PASS")
            c.post(
                "/key/block", json={"key": hashlib.sha256(raw.encode()).hexdigest()}
            ).raise_for_status()
            r = k.post(
                "/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "must reject"}],
                    "max_tokens": 1,
                },
            )
            assert r.status_code in (401, 403)
            print("Revoked key denied: PASS")
    finally:
        if raw:
            c.post(
                "/key/delete", json={"keys": [hashlib.sha256(raw.encode()).hexdigest()]}
            )
        c.post("/model/delete", json={"id": mid})
        server.shutdown()
