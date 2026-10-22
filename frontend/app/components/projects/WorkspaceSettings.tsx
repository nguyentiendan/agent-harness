"use client";
import { useState } from "react";
import { apiRequest } from "../../../lib/auth";
import type { Project } from "./ProjectDialog";
export type WorkspaceBinding = {
  mode: "managed" | "local";
  local_path: string | null;
  revision: number;
};

export function WorkspaceSettings({
  project,
  readOnly,
  onUpdated,
  onBusyChange,
}: {
  project: Project;
  readOnly: boolean;
  onUpdated: (p: Project) => void;
  onBusyChange: (busy: boolean) => void;
}) {
  const [path, setPath] = useState(project.workspace.local_path ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function connect(mode: "local" | "managed") {
    setBusy(true);
    onBusyChange(true);
    setError("");
    try {
      const p = await apiRequest<Project>(
        `/api/projects/${project.id}/workspace`,
        {
          method: "PUT",
          body: JSON.stringify({
            mode,
            local_path: mode === "local" ? path : null,
            expected_revision: project.workspace.revision,
          }),
        },
      );
      onUpdated(p);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  }
  return (
    <section className="mt-6 rounded-xl border border-zinc-200 bg-zinc-50 p-4 dark:border-zinc-700 dark:bg-zinc-900">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">Workspace</h3>
        <span className="rounded-full bg-teal-100 px-3 py-1 text-xs text-teal-800 dark:bg-teal-900 dark:text-teal-200">
          {project.workspace.mode === "local"
            ? "Local folder"
            : "Sandbox storage"}
        </span>
      </div>
      <p className="mt-2 break-all text-xs text-zinc-500">
        {project.workspace.local_path ??
          "Private, persistent storage for this project."}
      </p>
      <p className="mt-3 text-xs leading-relaxed text-zinc-500">
        Connect a folder on the PC running Docker. Agent edits are saved
        directly there. Switching storage does not copy or merge files;
        disconnecting restores this project’s sandbox storage.
      </p>
      <label className="ui-label mt-4" htmlFor="workspace-path">
        Local project folder
      </label>
      <input
        id="workspace-path"
        className="ui-field"
        value={path}
        onChange={(e) => setPath(e.target.value)}
        placeholder="/Users/yourname/projects/my-app"
        disabled={busy || readOnly}
        maxLength={4096}
        spellCheck={false}
      />
      {!readOnly && (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            className="ui-primary"
            disabled={busy || !path.startsWith("/")}
            onClick={() => connect("local")}
          >
            {busy ? "Checking…" : "Connect folder"}
          </button>
          {project.workspace.mode === "local" && (
            <button
              type="button"
              className="ui-button"
              disabled={busy}
              onClick={() => connect("managed")}
            >
              Disconnect folder
            </button>
          )}
        </div>
      )}
      {readOnly && (
        <p className="mt-3 text-xs text-zinc-500">
          Only the project owner can change its workspace.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-rose-600">
          {error}
        </p>
      )}
    </section>
  );
}
