"""Native filesystem tools and schemas."""

from __future__ import annotations


def register(registry, schema):
    def read_file(path: str, offset: int = 1, limit: int = 200) -> str:
        p = registry.safe_path(path)
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        offset = max(1, int(offset))
        limit = min(500, max(1, int(limit)))
        return "\n".join(lines[offset - 1:offset - 1 + limit])

    def write_file(path: str, content: str) -> str:
        p = registry.safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"

    def edit_file(path: str, old_text: str, new_text: str) -> str:
        p = registry.safe_path(path)
        text = p.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count != 1:
            return f"Error: expected exactly 1 occurrence of old_text, found {count}"
        p.write_text(text.replace(old_text, new_text), encoding="utf-8")
        return f"Edited {path}"

    def glob(pattern: str) -> str:
        import glob as _glob
        matches = sorted({
            m for m in _glob.glob(pattern, root_dir=registry.workspace_root, recursive=True)
            if (registry.workspace_root / m).resolve().is_relative_to(registry.workspace_root)
        })
        shown = matches[:200]
        if len(matches) > 200:
            shown.append("... (more matches omitted; narrow the pattern)")
        return "\n".join(shown) if shown else "(no matches)"

    def list_dir(path: str = ".") -> str:
        p = registry.safe_path(path)
        if not p.is_dir():
            return f"Not a directory: {path}"
        items = [f"{'[DIR]' if x.is_dir() else '[FILE]'} {x.name}" for x in sorted(p.iterdir())]
        return "\n".join(items) if items else "(empty)"

    def todo_write(todos: str) -> str:
        p = registry.workspace_root / "todos.json"
        p.write_text(todos, encoding="utf-8")
        return f"Updated todo list ({len(todos)} bytes)"

    registry.tools.update({
        'read_file': {"schema": schema(
                "read_file", "Read a UTF-8 text file inside the workspace.",
                {"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}},
                ["path"]), "handler": read_file, "mcp": False},
        'write_file': {"schema": schema(
                "write_file", "Write UTF-8 text to a file inside the workspace.",
                {"path": {"type": "string"}, "content": {"type": "string"}},
                ["path", "content"]), "handler": write_file, "mcp": False},
        'edit_file': {"schema": schema(
                "edit_file", "Replace one exact occurrence of text in a workspace file.",
                {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                ["path", "old_text", "new_text"]), "handler": edit_file, "mcp": False},
        'glob': {"schema": schema(
                "glob", "Find files matching a glob pattern; ** matches recursively.",
                {"pattern": {"type": "string"}}, ["pattern"]), "handler": glob, "mcp": False},
        'list_dir': {"schema": schema(
                "list_dir", "List files and directories inside the workspace.",
                {"path": {"type": "string"}}, []), "handler": list_dir, "mcp": False},
        'todo_write': {"schema": schema(
                "todo_write", "Update the task plan (todo list) as a JSON array of objects with 'content' and 'status' (pending/in_progress/completed).",
                {"todos": {"type": "string", "description": "JSON array of todo items"}}, ["todos"]),
                "handler": todo_write, "mcp": False},
    })
