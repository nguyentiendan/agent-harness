"""Native shell tools and schemas."""

from __future__ import annotations

import subprocess


def register(registry, schema):
    def bash(command: str) -> str:
        try:
            result = subprocess.run(
                command, shell=True, cwd=registry.workspace_root,
                capture_output=True, text=True, errors="replace",
                timeout=registry.bash_timeout, env=registry.bash_env(),
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {registry.bash_timeout}s"
        output = (result.stdout + result.stderr).strip()
        output = output[-30000:] if output else "(no output)"
        return f"exit_code={result.returncode}\n{output}"

    registry.tools.update({
        'bash': {"schema": schema(
                "bash", "Run a shell command inside the workspace and return exit code + output.",
                {"command": {"type": "string"}}, ["command"]), "handler": bash, "mcp": False},
    })
