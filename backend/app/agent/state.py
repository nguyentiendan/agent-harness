"""agent / state."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import json
import time


class StateManager:
    """Manages session state, checkpoints and the progress log."""

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir
        self.state_file = workspace_dir / "state.json"
        self.progress_file = workspace_dir / "progress.md"

    def load_state(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[StateManager] Warning: error loading state.json: {e}")
        return {"turn": 0, "messages": [], "completed_steps": [], "pending_tasks": []}

    def save_state(self, state_data: Dict[str, Any]):
        temp_file = self.state_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(state_data, f, ensure_ascii=False, indent=2)
        temp_file.replace(self.state_file)

    def log_progress(self, entry: str):
        """Append an activity entry to progress.md."""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        header = "# Workspace Progress Log\n\n" if not self.progress_file.exists() else ""
        with open(self.progress_file, "a", encoding="utf-8") as f:
            if header:
                f.write(header)
            f.write(f"- [{timestamp}] {entry}\n")
