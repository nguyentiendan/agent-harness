import unittest
import tempfile
import json
from pathlib import Path
from app.evaluation.dataset import load_dataset
from app.evaluation.verifiers import verify_file, compare_reports
from app.evaluation.runner import run_offline


class EvaluationTests(unittest.TestCase):
    def test_duplicate_case_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "cases.jsonl"
            case = {
                "id": "a",
                "category": "chat",
                "severity": "critical",
                "test": "test_prompts.PromptTests.test_missing_and_extra_variables_rejected",
            }
            path.write_text(json.dumps(case) + "\n" + json.dumps(case))
            with self.assertRaises(ValueError):
                load_dataset(str(path))

    def test_success_prose_cannot_override_file_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "file.txt"
            path.write_text("wrong")
            self.assertFalse(verify_file(root, "file.txt", "expected")["passed"])
            path.write_text("expected")
            self.assertTrue(verify_file(root, "file.txt", "expected")["passed"])
            self.assertFalse(verify_file(root, "../escape", "expected")["passed"])

    def test_critical_regression_detected(self):
        base = {"cases": [{"id": "x", "passed": True, "severity": "critical"}]}
        candidate = {"cases": [{"id": "x", "passed": False, "severity": "critical"}]}
        self.assertFalse(compare_reports(base, candidate)["passed"])

    def test_runner_accounts_for_failure_and_missing_test(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "cases.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "id": "missing",
                        "category": "chat",
                        "severity": "critical",
                        "test": "test_missing.NoTest.test_no",
                    }
                )
            )
            report = run_offline(str(path), str(Path(root) / "out"))
            self.assertEqual(len(report["cases"]), 1)
            self.assertFalse(report["cases"][0]["passed"])
            self.assertTrue(Path(report["cases"][0]["evidence"]).is_file())

    def test_budget_counts_pending_calls(self):
        from app.evaluation.budget import ExperimentBudget

        budget = ExperimentBudget("0.10")
        first = budget.claim("0.07")
        with self.assertRaises(ValueError):
            budget.claim("0.04")
        budget.settle(first, "0.02")
        budget.claim("0.08")
        with self.assertRaises(ValueError):
            budget.claim("0.01")

    def test_judge_invalid_score_is_failure(self):
        from app.evaluation.judges import validate_score

        with self.assertRaises(ValueError):
            validate_score({"score": 2, "reason": "good", "evidence_ids": []}, set())
        with self.assertRaises(ValueError):
            validate_score(
                {"score": 1, "reason": "good", "evidence_ids": ["invented"]}, {"a"}
            )

    def test_live_requires_disposable_database_and_budget(self):
        from app.evaluation.live import validate_live_environment

        with self.assertRaises(ValueError):
            validate_live_environment("postgresql://host/production", "1")
        with self.assertRaises(ValueError):
            validate_live_environment("sqlite:///evaluation_test.db", "0")

    def test_report_runs_without_git_binary(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "cases.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "id": "missing",
                        "category": "chat",
                        "severity": "critical",
                        "test": "test_missing.NoTest.test_no",
                    }
                )
            )
            with patch("shutil.which", return_value=None):
                report = run_offline(str(path), str(Path(root) / "out"))
            self.assertEqual(len(report["cases"]), 1)
            self.assertIsNone(report["git_revision"])
