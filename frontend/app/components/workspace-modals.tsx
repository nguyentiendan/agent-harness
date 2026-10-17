"use client";

import { useState } from "react";
import type { WorkspaceFileItem } from "../../lib/api";
import { ModalShell, SelectField } from "./controls";
import { Icon, PATHS } from "./icons";

const MODELS = ["gpt-4o-mini", "gpt-4o", "claude-3-7-sonnet-20250219", "deepseek/deepseek-chat", "deepseek/deepseek-reasoner", "gemini/gemini-1.5-pro", "ollama/llama3.3"];
const modelOptions = [{ value: "", label: "Use config default" }, ...MODELS.map((value) => ({ value, label: value }))];

export function SettingsModal(props: {
  apiBaseUrl: string; setApiBaseUrl: (v: string) => void;
  primaryModel: string; setPrimaryModel: (v: string) => void;
  reviewerModel: string; setReviewerModel: (v: string) => void;
  maxTurns: string; setMaxTurns: (v: string) => void;
  workspace: string; setWorkspace: (v: string) => void; workspaces: string[];
  onNewWorkspace: () => void; onClose: () => void;
}) {
  return <ModalShell title="Session settings" description="Choose how your agent works on the next run." icon={PATHS.gear} onClose={props.onClose}
    footer={<><span className="mr-auto text-xs text-zinc-500">Changes apply to the next run</span><button type="button" onClick={props.onClose} className="ui-primary"><Icon d={PATHS.check} />Done</button></>}>
    <div className="space-y-6">
      <section><h3 className="mb-4 text-xs font-semibold tracking-widest text-teal-700 uppercase dark:text-teal-300">Models & execution</h3>
        <div className="space-y-4">
          <div><label htmlFor="primary-model" className="ui-label">Primary agent</label><SelectField id="primary-model" label="Primary agent model" value={props.primaryModel} onChange={props.setPrimaryModel} options={modelOptions} /><p className="mt-1.5 text-xs text-zinc-500">Plans the work and executes tools.</p></div>
          <div><label htmlFor="reviewer-model" className="ui-label">Reviewer</label><SelectField id="reviewer-model" label="Reviewer model" value={props.reviewerModel} onChange={props.setReviewerModel} options={modelOptions} /><p className="mt-1.5 text-xs text-zinc-500">Checks whether the goal has been achieved.</p></div>
          <div className="flex items-center justify-between gap-5 rounded-xl bg-zinc-50 p-4 dark:bg-zinc-800/50"><div><label htmlFor="max-turns" className="ui-label mb-1">Maximum turns</label><p className="text-xs text-zinc-500">Limit the steps in each agent run.</p></div><input id="max-turns" className="ui-field max-w-24" type="number" min={1} step={1} value={props.maxTurns} onChange={(e) => props.setMaxTurns(e.target.value)} onBlur={() => { if (props.maxTurns && Number(props.maxTurns) < 1) props.setMaxTurns("1"); }} /></div>
        </div>
      </section>
      <section className="border-t border-zinc-200 pt-5 dark:border-zinc-800"><h3 className="mb-4 text-xs font-semibold tracking-widest text-teal-700 uppercase dark:text-teal-300">Workspace</h3>
        <div className="flex gap-2"><div className="min-w-0 flex-1"><SelectField label="Workspace" value={props.workspace} onChange={props.setWorkspace} options={props.workspaces.map((value) => ({ value, label: value }))} /></div><button type="button" onClick={props.onNewWorkspace} className="ui-button"><Icon d={PATHS.plus} />New</button></div>
        <p className="mt-2 text-xs text-zinc-500">Switching workspace starts a new conversation.</p>
      </section>
      <details className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-700"><summary className="cursor-pointer text-sm font-medium focus-visible:outline-2 focus-visible:outline-teal-600">Advanced connection</summary><div className="mt-4"><label htmlFor="api-url" className="ui-label">API base URL</label><input id="api-url" className="ui-field" value={props.apiBaseUrl} onChange={(e) => props.setApiBaseUrl(e.target.value)} placeholder="Use this application's server" /><p className="mt-2 text-xs text-zinc-500">Leave empty to use the default server.</p></div></details>
    </div>
  </ModalShell>;
}

export function CreateWorkspaceModal({ onCreate, onClose, existing }: { onCreate: (name: string) => Promise<void>; onClose: () => void; existing: string[] }) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); if (busy) return;
    const value = name.trim();
    if (!value || value.startsWith(".") || /[/\\\x00]/.test(value)) { setError("Enter a name without slashes or a leading dot."); return; }
    if (existing.includes(value)) { setError("A workspace with this name already exists."); return; }
    setBusy(true); setError("");
    try { await onCreate(value); onClose(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not create workspace. Try again."); }
    finally { setBusy(false); }
  };
  return <ModalShell title="Create a workspace" description="Give your next project a dedicated place to work." icon={PATHS.folder} onClose={() => { if (!busy) onClose(); }}
    footer={<><button type="button" className="ui-button" disabled={busy} onClick={onClose}>Cancel</button><button type="submit" form="create-workspace-form" className="ui-primary" disabled={busy || !name.trim()}><Icon d={PATHS.plus} />{busy ? "Creating…" : "Create workspace"}</button></>}>
    <form id="create-workspace-form" onSubmit={submit} className="space-y-5" aria-busy={busy}>
      <div><label htmlFor="workspace-name" className="ui-label">Workspace name</label><input id="workspace-name" autoFocus className="ui-field" placeholder="e.g. Marketing research" value={name} disabled={busy} aria-invalid={!!error} aria-describedby={error ? "workspace-error" : "workspace-hint"} onChange={(e) => { setName(e.target.value); setError(""); }} /><p id="workspace-hint" className="mt-2 text-xs text-zinc-500">Choose a short, recognizable project name.</p>{error && <p id="workspace-error" role="alert" className="mt-2 text-sm text-rose-600 dark:text-rose-400">{error}</p>}</div>
      <div className="flex items-start gap-3 rounded-xl border border-teal-600/15 bg-teal-50 p-4 text-sm leading-relaxed text-teal-800 dark:bg-teal-500/10 dark:text-teal-200"><span className="mt-0.5 shrink-0"><Icon d={PATHS.folder} size={20} /></span><p>Conversations and files created by the agent will be associated with this workspace.</p></div>
    </form>
  </ModalShell>;
}

function sizeLabel(bytes: number) { return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
export function FilesModal(props: {
  files: WorkspaceFileItem[]; fileView: { path: string; content: string } | null;
  onOpen: (path: string) => void; onBack: () => void; onClose: () => void;
  workspace: string; loading: boolean; error: string; onRefresh: () => void;
}) {
  const [query, setQuery] = useState("");
  const visible = props.files.filter((file) => file.path.toLowerCase().includes(query.toLowerCase()));
  return <ModalShell wide title="Workspace files" description={`Browse and preview files in ${props.workspace}.`} icon={PATHS.folder} onClose={props.onClose}
    footer={<><span className="mr-auto text-xs text-zinc-500">{props.files.length} files · Read-only preview</span><button type="button" className="ui-button" onClick={props.onClose}>Close</button></>}>
    <div className="mb-4 flex gap-2"><input className="ui-field" aria-label="Search files" placeholder="Search by file name…" value={query} onChange={(e) => setQuery(e.target.value)} /><button type="button" className="ui-button" disabled={props.loading} onClick={props.onRefresh}>Refresh</button></div>
    {props.error && <p role="alert" className="mb-4 rounded-xl bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{props.error}</p>}
    <div className="grid min-h-72 gap-4 md:grid-cols-[240px_minmax(0,1fr)]">
      <div className="max-h-80 overflow-y-auto rounded-xl border border-zinc-200 p-1.5 dark:border-zinc-700" aria-busy={props.loading}>
        {props.loading && <p role="status" className="p-4 text-sm text-zinc-500">Loading files…</p>}
        {!props.loading && !visible.length && <div className="px-4 py-12 text-center text-sm text-zinc-500">{query ? "No matching files." : "No files in this workspace yet."}</div>}
        {visible.map((file) => <button type="button" key={file.path} onClick={() => props.onOpen(file.path)} aria-pressed={props.fileView?.path === file.path} className={`mb-1 flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-teal-600 ${props.fileView?.path === file.path ? "bg-teal-50 text-teal-800 dark:bg-teal-500/15 dark:text-teal-200" : "hover:bg-zinc-100 dark:hover:bg-zinc-800"}`}><span className="shrink-0 text-teal-600 dark:text-teal-400"><Icon d={PATHS.file} size={19} /></span><span className="min-w-0"><span className="block truncate text-sm font-medium" title={file.path}>{file.path}</span><span className="text-xs text-zinc-500">{sizeLabel(file.size)}</span></span></button>)}
      </div>
      <div className="min-w-0 overflow-hidden rounded-xl border border-zinc-200 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-950/40">
        {props.fileView ? <><div className="flex items-center gap-3 border-b border-zinc-200 bg-white px-4 py-3 dark:border-zinc-700 dark:bg-zinc-900"><span className="min-w-0 flex-1 truncate text-sm font-medium" title={props.fileView.path}>{props.fileView.path}</span><button type="button" aria-label="Clear preview" onClick={props.onBack} className="rounded p-1 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"><Icon d={PATHS.x} size={16} /></button></div><pre className="max-h-72 overflow-auto p-4 font-mono text-xs leading-6 text-zinc-700 dark:text-zinc-300">{props.fileView.content || "(empty file)"}</pre></> : <div className="flex h-full min-h-60 flex-col items-center justify-center gap-3 p-6 text-center"><span className="rounded-2xl bg-white p-4 text-zinc-400 shadow-sm dark:bg-zinc-800"><Icon d={PATHS.file} size={28} /></span><p className="text-sm font-medium">Select a file to preview</p><p className="max-w-52 text-xs leading-relaxed text-zinc-500">Files created by your agent appear here.</p></div>}
      </div>
    </div>
  </ModalShell>;
}
