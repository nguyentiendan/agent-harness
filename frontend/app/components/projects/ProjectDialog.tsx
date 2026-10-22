"use client";
import { useState } from "react";
import { ModalShell } from "../controls";
import { apiRequest } from "../../../lib/auth";
import { WorkspaceSettings, type WorkspaceBinding } from "./WorkspaceSettings";
export interface Project {
  workspace: WorkspaceBinding;
  id: string;
  name: string;
  instructions: string;
  owner_id: string;
  owner_username: string;
}
export function ProjectDialog({
  project,
  onClose,
  onSaved,
  onDeleted,
  onUpdated,
  readOnly = false,
}: {
  project: Project | null;
  onUpdated: (p: Project) => void;
  readOnly?: boolean;
  onClose: () => void;
  onSaved: (p: Project) => void;
  onDeleted: (id: string) => void;
}) {
  const [name, setName] = useState(project?.name ?? "");
  const [instructions, setInstructions] = useState(project?.instructions ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);
  async function save() {
    setBusy(true);
    setError("");
    try {
      const p = await apiRequest<Project>(
        project ? `/api/projects/${project.id}` : "/api/projects",
        {
          method: project ? "PATCH" : "POST",
          body: JSON.stringify({ name, instructions }),
        },
      );
      onSaved(p);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!project) return;
    setBusy(true);
    setError("");
    try {
      await apiRequest(`/api/projects/${project.id}`, { method: "DELETE" });
      onDeleted(project.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <ModalShell
      title={project ? "Project settings" : "New project"}
      description="A private place for related chats, shared files and instructions."
      onClose={() => {
        if (!busy) onClose();
      }}
      footer={
        <>
          <button className="ui-button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button
            className="ui-primary"
            disabled={busy || readOnly || !name.trim()}
            onClick={save}
          >
            {busy ? "Saving…" : "Save project"}
          </button>
        </>
      }
    >
      <label className="ui-label" htmlFor="project-name">
        Project name
      </label>
      <input
        id="project-name"
        disabled={busy || readOnly}
        autoFocus
        className="ui-field"
        maxLength={120}
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <label className="ui-label mt-5" htmlFor="project-instructions">
        Instructions
      </label>
      <textarea
        disabled={busy || readOnly}
        id="project-instructions"
        className="ui-field min-h-36"
        maxLength={16000}
        placeholder="What should the AI know for every chat in this project?"
        value={instructions}
        onChange={(e) => setInstructions(e.target.value)}
      />
      <p className="mt-2 text-xs text-zinc-500">
        Chats share these instructions and files. Each conversation keeps its
        own history.
      </p>
      {project ? (
        <WorkspaceSettings
          project={project}
          readOnly={readOnly || busy}
          onUpdated={onUpdated}
          onBusyChange={setBusy}
        />
      ) : (
        <p className="mt-4 rounded-xl bg-teal-50 p-3 text-xs text-teal-800 dark:bg-teal-900/20 dark:text-teal-200">
          Your project starts with private sandbox storage. After creation, open
          Project settings to connect a local folder.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-4 text-sm text-rose-600">
          {error}
        </p>
      )}
      {project && !readOnly && (
        <div className="mt-6 border-t border-zinc-200 pt-4 dark:border-zinc-800">
          {confirm ? (
            <>
              <p className="mb-3 text-sm text-rose-600">
                Delete “{project.name}” and all its chats? Managed files go to
                trash for 7 days, then are permanently removed. Connected local
                folders and their files remain on your PC.
              </p>
              <button
                className="ui-button text-rose-600"
                disabled={busy}
                onClick={remove}
              >
                Delete project and chats
              </button>
              <button
                className="ui-button ml-2"
                disabled={busy}
                onClick={() => setConfirm(false)}
              >
                Keep project
              </button>
            </>
          ) : (
            <button
              className="text-sm text-rose-600"
              disabled={busy}
              onClick={() => setConfirm(true)}
            >
              Delete project…
            </button>
          )}
        </div>
      )}
    </ModalShell>
  );
}
