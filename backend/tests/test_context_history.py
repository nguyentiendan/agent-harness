import support
import unittest
from app.platform.db import Base, engine, DB
from app.context.history import append_records, load_groups, bounded_output
from app.context.types import ConversationRecord


class ContextHistoryTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    def test_idempotent_scoped_history(self):
        with DB.begin() as db:
            records = [
                ConversationRecord(
                    "u", "user", {"role": "user", "content": "hello"}, "g"
                )
            ]
            append_records(db, "s", "r", records)
            append_records(db, "s", "r", records)
        with DB() as db:
            self.assertEqual(len(load_groups(db, "s")), 1)
            self.assertEqual(load_groups(db, "other"), [])

    def test_incomplete_tool_group_excluded(self):
        with DB.begin() as db:
            append_records(
                db,
                "s",
                "r",
                [
                    ConversationRecord(
                        "a",
                        "assistant",
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "t",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                        "g",
                    )
                ],
            )
        with DB() as db:
            self.assertEqual(load_groups(db, "s"), [])
        with DB.begin() as db:
            append_records(
                db,
                "s",
                "r",
                [
                    ConversationRecord(
                        "t",
                        "tool",
                        {"role": "tool", "tool_call_id": "t", "content": "ok"},
                        "g",
                    )
                ],
            )
        with DB() as db:
            self.assertEqual(len(load_groups(db, "s")[0].messages), 2)

    def test_truncated_output_stays_json(self):
        import json

        output = bounded_output({"stdout": "あ" * 1000, "exit_code": 0}, 200)
        data = json.loads(output)
        self.assertTrue(data["truncated"])
        self.assertEqual(data["exit_code"], 0)
        self.assertLessEqual(len(output), 200)
