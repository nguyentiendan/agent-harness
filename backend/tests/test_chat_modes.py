import support
import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, MagicMock
import test_platform_auth as auth
from app.platform.db import DB, Chat, Run, ExecutionScope
from app.platform import chat
from app.execution.lifecycle import prepare


class ChatModeTests(unittest.TestCase):
    setUp = auth.PlatformTests.setUp
    login = auth.PlatformTests.login
    new_chat = auth.PlatformTests.new_chat

    def test_standalone_no_tools_even_unexpected_tool_call(self):
        self.login()
        sid = self.new_chat()
        with DB.begin() as db:
            db.add(
                Run(
                    id="text-run",
                    session_id=sid,
                    request_id="one",
                    body_hash="x",
                    model="fake",
                    event_offset=0,
                )
            )
        result = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id="bad",
                                function=SimpleNamespace(
                                    name="bash", arguments='{"command":"touch x"}'
                                ),
                            )
                        ],
                    )
                )
            ]
        )
        runner = MagicMock()
        with (
            patch.dict("os.environ", SANDBOX_ENABLED="true"),
            patch("app.execution.client.RunnerClient", return_value=runner),
            patch(
                "app.platform.chat.LiteLLMClientWrapper.complete",
                new=AsyncMock(return_value=result),
            ) as complete,
        ):
            asyncio.run(chat.execute("text-run"))
        self.assertNotIn("tools", complete.call_args.kwargs)
        runner.ensure.assert_not_called()
        runner.execute.assert_not_called()
        with DB() as db:
            self.assertIsNone(db.get(ExecutionScope, sid))
            self.assertIn("Project", chat.events_for(db, sid)[0]["text"])

    def test_prepare_rejects_standalone(self):
        self.login()
        sid = self.new_chat()
        with DB.begin() as db:
            db.add(
                Run(
                    id="direct",
                    session_id=sid,
                    request_id="one",
                    body_hash="x",
                    model="fake",
                    event_offset=0,
                )
            )
        with patch("app.execution.client.RunnerClient") as runner:
            with self.assertRaises(RuntimeError):
                prepare("direct")
            runner.assert_not_called()

    def test_standalone_files_only_legacy_export(self):
        self.login()
        sid = self.new_chat()
        for method, path in [
            ("get", "files"),
            ("get", "read"),
            ("get", "files/download"),
            ("post", "files/upload"),
            ("delete", "files"),
        ]:
            self.assertEqual(
                getattr(self.client, method)(
                    f"/api/sessions/{sid}/{path}?path=a"
                ).status_code,
                403,
            )
        with DB.begin() as db:
            db.add(
                ExecutionScope(
                    id=sid, owner_id=self.a, standalone_session_id=sid, has_files=True
                )
            )
        with (
            patch(
                "app.execution.client.RunnerClient.files", return_value={"files": []}
            ),
            patch("app.execution.client.RunnerClient.configure"),
        ):
            self.assertEqual(
                self.client.get(f"/api/sessions/{sid}/files").status_code, 200
            )
        self.assertEqual(
            self.client.delete(f"/api/sessions/{sid}/files?path=a").status_code, 403
        )
        with DB() as db:
            self.assertTrue(
                chat.chat_dto(db, db.get(Chat, sid))["legacy_files_available"]
            )

    def test_legacy_download_preserves_history_and_scope(self):
        import httpx
        from app.platform.db import Event

        self.login()
        sid = self.new_chat()
        with DB.begin() as db:
            db.add(
                ExecutionScope(
                    id=sid, owner_id=self.a, standalone_session_id=sid, has_files=True
                )
            )
            db.add(
                Run(
                    id="legacy",
                    session_id=sid,
                    request_id="legacy",
                    body_hash="x",
                    model="fake",
                    event_offset=0,
                    status="completed",
                )
            )
            db.flush()
            chat.append_event(
                db, sid, "legacy", {"type": "user", "text": "old history"}
            )

        def connection():
            return httpx.AsyncClient(
                base_url="http://runner",
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(200, content=b"old file")
                ),
            )

        with (
            patch("app.execution.client.RunnerClient.configure") as configure,
            patch("app.platform.files.connection", side_effect=connection),
        ):
            response = self.client.get(
                f"/api/sessions/{sid}/files/download?path=old.txt"
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, b"old file")
            self.assertEqual(
                configure.call_args.args[2],
                dict(mode="managed", local_path=None, revision=0),
            )
        with DB() as db:
            self.assertEqual(chat.events_for(db, sid)[0]["text"], "old history")
            self.assertTrue(db.get(ExecutionScope, sid).has_files)
