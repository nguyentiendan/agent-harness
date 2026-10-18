"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "../../lib/auth";
import type { WorkspaceFileItem } from "../../lib/api";
import { ModalShell, SelectField } from "./controls";
import { Icon, PATHS } from "./icons";

export function SettingsModal(props: {
  primaryModel: string;
  setPrimaryModel: (v: string) => void;
  onClose: () => void;
}) {
  const [models, setModels] = useState<string[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    apiRequest<{ models: string[] }>("/api/config")
      .then((r) => setModels(r.models))
      .catch((e) => setError(e.message));
  }, []);
  return (
    <ModalShell
      title="Chat settings"
      description="Choose the model for your next message."
      onClose={props.onClose}
      footer={
        <button className="ui-primary" onClick={props.onClose}>
          Done
        </button>
      }
    >
      <label className="ui-label">Model</label>
      <SelectField
        label="Chat model"
        value={props.primaryModel}
        onChange={props.setPrimaryModel}
        options={[
          { value: "", label: "Server default" },
          ...models.map((value) => ({ value, label: value })),
        ]}
      />
      {error && (
        <p role="alert" className="mt-3 text-sm text-rose-600">
          {error}
        </p>
      )}
      <p className="mt-4 text-sm text-zinc-500">
        Text chat is enabled. Code execution and file tools will be available
        after sandbox setup.
      </p>
    </ModalShell>
  );
}

function sizeLabel(bytes: number) {
  return bytes < 1024
    ? `${bytes} B`
    : bytes < 1024 * 1024
      ? `${(bytes / 1024).toFixed(1)} KB`
      : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
export function FilesModal(props: {
  files: WorkspaceFileItem[];
  fileView: { path: string; content: string } | null;
  onOpen: (path: string) => void;
  onBack: () => void;
  onClose: () => void;
  workspace: string;
  loading: boolean;
  error: string;
  onRefresh: () => void;
}) {
  const [query, setQuery] = useState("");
  const visible = props.files.filter((file) =>
    file.path.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <ModalShell
      wide
      title="Chat files"
      description={`Browse and preview files in ${props.workspace}.`}
      icon={PATHS.folder}
      onClose={props.onClose}
      footer={
        <>
          <span className="mr-auto text-xs text-zinc-500">
            {props.files.length} files · Read-only preview
          </span>
          <button type="button" className="ui-button" onClick={props.onClose}>
            Close
          </button>
        </>
      }
    >
      <div className="mb-4 flex gap-2">
        <input
          className="ui-field"
          aria-label="Search files"
          placeholder="Search by file name…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button
          type="button"
          className="ui-button"
          disabled={props.loading}
          onClick={props.onRefresh}
        >
          Refresh
        </button>
      </div>
      {props.error && (
        <p
          role="alert"
          className="mb-4 rounded-xl bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"
        >
          {props.error}
        </p>
      )}
      <div className="grid min-h-72 gap-4 md:grid-cols-[240px_minmax(0,1fr)]">
        <div
          className="max-h-80 overflow-y-auto rounded-xl border border-zinc-200 p-1.5 dark:border-zinc-700"
          aria-busy={props.loading}
        >
          {props.loading && (
            <p role="status" className="p-4 text-sm text-zinc-500">
              Loading files…
            </p>
          )}
          {!props.loading && !visible.length && (
            <div className="px-4 py-12 text-center text-sm text-zinc-500">
              {query
                ? "No matching files."
                : "File tools will be available after sandbox setup."}
            </div>
          )}
          {visible.map((file) => (
            <button
              type="button"
              key={file.path}
              onClick={() => props.onOpen(file.path)}
              aria-pressed={props.fileView?.path === file.path}
              className={`mb-1 flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-teal-600 ${props.fileView?.path === file.path ? "bg-teal-50 text-teal-800 dark:bg-teal-500/15 dark:text-teal-200" : "hover:bg-zinc-100 dark:hover:bg-zinc-800"}`}
            >
              <span className="shrink-0 text-teal-600 dark:text-teal-400">
                <Icon d={PATHS.file} size={19} />
              </span>
              <span className="min-w-0">
                <span
                  className="block truncate text-sm font-medium"
                  title={file.path}
                >
                  {file.path}
                </span>
                <span className="text-xs text-zinc-500">
                  {sizeLabel(file.size)}
                </span>
              </span>
            </button>
          ))}
        </div>
        <div className="min-w-0 overflow-hidden rounded-xl border border-zinc-200 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-950/40">
          {props.fileView ? (
            <>
              <div className="flex items-center gap-3 border-b border-zinc-200 bg-white px-4 py-3 dark:border-zinc-700 dark:bg-zinc-900">
                <span
                  className="min-w-0 flex-1 truncate text-sm font-medium"
                  title={props.fileView.path}
                >
                  {props.fileView.path}
                </span>
                <button
                  type="button"
                  aria-label="Clear preview"
                  onClick={props.onBack}
                  className="rounded p-1 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                >
                  <Icon d={PATHS.x} size={16} />
                </button>
              </div>
              <pre className="max-h-72 overflow-auto p-4 font-mono text-xs leading-6 text-zinc-700 dark:text-zinc-300">
                {props.fileView.content || "(empty file)"}
              </pre>
            </>
          ) : (
            <div className="flex h-full min-h-60 flex-col items-center justify-center gap-3 p-6 text-center">
              <span className="rounded-2xl bg-white p-4 text-zinc-400 shadow-sm dark:bg-zinc-800">
                <Icon d={PATHS.file} size={28} />
              </span>
              <p className="text-sm font-medium">Select a file to preview</p>
              <p className="max-w-52 text-xs leading-relaxed text-zinc-500">
                Files created by your agent appear here.
              </p>
            </div>
          )}
        </div>
      </div>
    </ModalShell>
  );
}
