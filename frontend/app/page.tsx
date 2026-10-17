"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createWorkspace,
  getSessionEvents,
  listSessions,
  listWorkspaceFiles,
  listWorkspaces,
  readWorkspaceFile,
  resolveApproval,
  runCommand,
  startHarness,
  stopHarness,
  streamEvents,
  type HarnessEvent,
  type HarnessStatus,
  type SessionSummary,
  type WorkspaceFileItem,
} from "../lib/api";

type ChatMessage =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | {
      kind: "tool";
      name: string;
      args: Record<string, unknown>;
      output: string;
      done: boolean;
      open: boolean;
    }
  | {
      kind: "approval";
      id: string;
      name: string;
      args: Record<string, unknown>;
      reason: string;
      resolved?: "allowed" | "denied";
    }
  | { kind: "status"; status: string; reason?: string }
  | { kind: "system"; text: string };

const MODEL_OPTIONS = [
  "gpt-4o-mini",
  "gpt-4o",
  "claude-3-7-sonnet-20250219",
  "deepseek/deepseek-chat",
  "deepseek/deepseek-reasoner",
  "gemini/gemini-1.5-pro",
  "ollama/llama3.3",
];

function timeAgo(ts: number): string {
  if (!ts) return "";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function lastToolIndex(msgs: ChatMessage[]): number {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m.kind === "tool" && !m.done) return i;
  }
  return -1;
}

function lastApprovalIndex(msgs: ChatMessage[]): number {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m.kind === "approval" && !m.resolved) return i;
  }
  return -1;
}

export default function Home() {
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [primaryModel, setPrimaryModel] = useState("");
  const [reviewerModel, setReviewerModel] = useState("");
  const [maxTurns, setMaxTurns] = useState("5");
  const [workspace, setWorkspace] = useState("default");
  const [settingsOpen, setSettingsOpen] = useState(false);

  const [workspaces, setWorkspaces] = useState<string[]>(["default"]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState("");
  const [status, setStatus] = useState<"idle" | HarnessStatus>("idle");

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");

  const [filesOpen, setFilesOpen] = useState(false);
  const [files, setFiles] = useState<WorkspaceFileItem[]>([]);
  const [fileView, setFileView] = useState<{ path: string; content: string } | null>(null);

  const closeStreamRef = useRef<(() => void) | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  const refreshWorkspaces = useCallback(async () => {
    try {
      const r = await listWorkspaces(apiBaseUrl);
      setWorkspaces(r.workspaces);
    } catch {
      // backend not ready
    }
  }, [apiBaseUrl]);

  const refreshSessions = useCallback(async () => {
    try {
      const r = await listSessions(apiBaseUrl);
      setSessions(r.sessions);
    } catch {
      // backend not ready
    }
  }, [apiBaseUrl]);

  const refreshFiles = useCallback(async () => {
    if (!currentSessionId) return;
    try {
      const r = await listWorkspaceFiles(apiBaseUrl, currentSessionId);
      setFiles(r.files);
    } catch {
      // ignore
    }
  }, [apiBaseUrl, currentSessionId]);

  useEffect(() => {
    refreshWorkspaces();
    refreshSessions();
  }, [refreshWorkspaces, refreshSessions]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    return () => closeStreamRef.current?.();
  }, []);

  const applyEvent = useCallback(
    (ev: HarnessEvent) => {
      switch (ev.type) {
        case "tool_call":
          setMessages((m) => [
            ...m,
            {
              kind: "tool",
              name: String(ev.name ?? ""),
              args: (ev.args as Record<string, unknown>) ?? {},
              output: "",
              done: false,
              open: true,
            },
          ]);
          break;
        case "tool_result":
          setMessages((m) => {
            const idx = lastToolIndex(m);
            if (idx < 0) return m;
            const next = m.slice();
            next[idx] = {
              ...(next[idx] as Extract<ChatMessage, { kind: "tool" }>),
              output: String(ev.output ?? ""),
              done: true,
            };
            return next;
          });
          break;
        case "assistant":
          setMessages((m) => [...m, { kind: "assistant", text: String(ev.text ?? "") }]);
          break;
        case "approval":
          setMessages((m) => [
            ...m,
            {
              kind: "approval",
              id: String(ev.approval_id ?? ""),
              name: String(ev.name ?? ""),
              args: (ev.args as Record<string, unknown>) ?? {},
              reason: String(ev.reason ?? ""),
            },
          ]);
          break;
        case "approval_result":
          setMessages((m) => {
            const idx = lastApprovalIndex(m);
            if (idx < 0) return m;
            const next = m.slice();
            next[idx] = {
              ...(next[idx] as Extract<ChatMessage, { kind: "approval" }>),
              resolved: ev.allowed ? "allowed" : "denied",
            };
            return next;
          });
          break;
        case "status":
          setStatus(String(ev.status ?? "idle") as HarnessStatus);
          setMessages((m) => [
            ...m,
            { kind: "status", status: String(ev.status ?? ""), reason: String(ev.reason ?? "") },
          ]);
          closeStreamRef.current?.();
          refreshSessions();
          refreshFiles();
          break;
      }
    },
    [refreshSessions, refreshFiles],
  );

  const send = async () => {
    const text = input.trim();
    if (!text) return;
    if (text.startsWith("/")) {
      setInput("");
      await handleCommand(text);
      return;
    }
    if (status === "running") return;
    const id = `session_${Date.now()}`;
    setInput("");
    setCurrentSessionId(id);
    setStatus("running");
    setMessages((m) => [...m, { kind: "user", text }]);
    try {
      await startHarness(apiBaseUrl, {
        session_id: id,
        goal: text,
        max_turns: maxTurns === "" ? null : Number(maxTurns),
        primary_model: primaryModel || null,
        reviewer_model: reviewerModel || null,
        workspace_path: workspace || null,
      });
      closeStreamRef.current?.();
      closeStreamRef.current = streamEvents(apiBaseUrl, id, applyEvent);
    } catch (e) {
      setStatus("failed");
      setMessages((m) => [
        ...m,
        { kind: "status", status: "failed", reason: e instanceof Error ? e.message : String(e) },
      ]);
    }
    refreshSessions();
  };

  const stopRun = async () => {
    if (!currentSessionId) return;
    try {
      await stopHarness(apiBaseUrl, currentSessionId);
      setStatus("stopped");
    } catch {
      // ignore
    }
  };

  const pushSystem = (text: string) => setMessages((m) => [...m, { kind: "system", text }]);

  /** Slash commands: some run locally, the rest are handled by the backend. */
  const handleCommand = async (raw: string) => {
    const trimmed = raw.trim();
    const spaceIdx = trimmed.indexOf(" ");
    const cmd = (spaceIdx === -1 ? trimmed.slice(1) : trimmed.slice(1, spaceIdx)).toLowerCase();
    const args = spaceIdx === -1 ? "" : trimmed.slice(spaceIdx + 1).trim();

    switch (cmd) {
      case "new":
      case "reset":
        closeStreamRef.current?.();
        closeStreamRef.current = null;
        setMessages([]);
        setCurrentSessionId("");
        setStatus("idle");
        pushSystem("Started a new conversation.");
        return;
      case "stop":
        await stopRun();
        return;
      case "settings":
        setSettingsOpen(true);
        return;
      case "files":
        setFilesOpen(true);
        setFileView(null);
        refreshFiles();
        return;
      case "model":
        if (!args) {
          pushSystem(`Usage: /model <model-name>  (current: ${primaryModel || "config default"})`);
          return;
        }
        setPrimaryModel(args);
        pushSystem(`Primary model set to: ${args}`);
        return;
      default:
        try {
          const r = await runCommand(apiBaseUrl, currentSessionId, cmd, args);
          pushSystem(r.output);
        } catch (e) {
          pushSystem(`Command failed: ${e instanceof Error ? e.message : String(e)}`);
        }
        return;
    }
  };

  const respondApproval = async (approvalId: string, allow: boolean) => {
    setMessages((m) =>
      m.map((msg) =>
        msg.kind === "approval" && msg.id === approvalId
          ? { ...msg, resolved: allow ? "allowed" : "denied" }
          : msg,
      ),
    );
    try {
      await resolveApproval(apiBaseUrl, approvalId, allow);
    } catch {
      // ignore
    }
  };

  const loadSession = async (sid: string) => {
    setCurrentSessionId(sid);
    setMessages([]);
    closeStreamRef.current?.();
    const rec = sessions.find((s) => s.session_id === sid);
    if (rec) {
      setStatus((rec.status as HarnessStatus) || "idle");
      setMessages([{ kind: "user", text: rec.goal }]);
    }
    try {
      const r = await getSessionEvents(apiBaseUrl, sid);
      for (const ev of r.events) applyEvent(ev);
    } catch {
      // ignore
    }
    refreshFiles();
  };

  const newWorkspace = async () => {
    const name = window.prompt("New workspace name:");
    if (!name || !name.trim()) return;
    try {
      await createWorkspace(apiBaseUrl, name.trim());
      await refreshWorkspaces();
      setWorkspace(name.trim());
    } catch (e) {
      window.alert(e instanceof Error ? e.message : String(e));
    }
  };

  const openFile = async (path: string) => {
    if (!currentSessionId) return;
    try {
      const r = await readWorkspaceFile(apiBaseUrl, currentSessionId, path);
      setFileView({ path: r.path, content: r.content });
    } catch {
      // ignore
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const currentMetrics = sessions.find((s) => s.session_id === currentSessionId)?.metrics;

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar-header">
          <h1>AI Task Harness</h1>
          <div className="workspace-select">
            <select value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
              {workspaces.map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
            <button className="btn-icon" onClick={newWorkspace}>
              + New
            </button>
          </div>
        </div>

        <div className="sessions">
          <div className="sessions-label">Sessions</div>
          {sessions.length === 0 && (
            <div className="session-meta" style={{ padding: 8 }}>
              No sessions yet
            </div>
          )}
          {sessions.map((s) => (
            <button
              key={s.session_id}
              className={`session-item ${s.session_id === currentSessionId ? "active" : ""}`}
              onClick={() => loadSession(s.session_id)}
            >
              <div className="session-title">{s.goal}</div>
              <div className="session-meta">
                <span className={`dot ${s.status}`} />
                <span>{s.status}</span>
                <span>{timeAgo(s.updated_at)}</span>
              </div>
            </button>
          ))}
        </div>

        <div className="sidebar-footer">
          <button className="btn-icon" onClick={() => setSettingsOpen(true)}>
            ⚙ Settings
          </button>
          <button
            className="btn-icon"
            onClick={() => {
              setFilesOpen(true);
              setFileView(null);
              refreshFiles();
            }}
          >
            📁 Files
          </button>
        </div>
      </aside>

      <main className="main">
        <div className="chat-header">
          <div className="title">Workspace: {workspace}</div>
          <span className={`pill ${status}`}>
            <span className="dot" />
            {status}
          </span>
          {currentMetrics && currentMetrics.tool_calls !== undefined && (
            <span className="meta">
              {currentMetrics.duration_seconds ?? 0}s · {currentMetrics.tool_calls} tool calls ·{" "}
              {currentMetrics.turns ?? 0} turns
              {currentMetrics.model ? ` · ${currentMetrics.model}` : ""}
            </span>
          )}
          {status === "running" && (
            <button className="btn btn-danger" onClick={stopRun}>
              Stop
            </button>
          )}
          <button className="btn btn-secondary" onClick={() => setSettingsOpen(true)}>
            Settings
          </button>
        </div>

        <div className="messages">
          {messages.length === 0 && (
            <div className="empty-hint">
              Send a goal to start. The agent will work in the “{workspace}” workspace.
            </div>
          )}
          {messages.map((m, i) => (
            <MessageView key={i} m={m} onRespond={respondApproval} />
          ))}
          <div ref={endRef} />
        </div>

        <div className="input-bar">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Type a goal and press Enter…"
            rows={1}
          />
        </div>
        <div className="input-hint" style={{ padding: "0 20px 12px" }}>
          Enter to send · Shift+Enter for a new line · type <code>/help</code> for commands
        </div>
      </main>

      {settingsOpen && (
        <SettingsModal
          apiBaseUrl={apiBaseUrl}
          setApiBaseUrl={setApiBaseUrl}
          primaryModel={primaryModel}
          setPrimaryModel={setPrimaryModel}
          reviewerModel={reviewerModel}
          setReviewerModel={setReviewerModel}
          maxTurns={maxTurns}
          setMaxTurns={setMaxTurns}
          workspace={workspace}
          setWorkspace={setWorkspace}
          workspaces={workspaces}
          onNewWorkspace={newWorkspace}
          onClose={() => setSettingsOpen(false)}
        />
      )}

      {filesOpen && (
        <FilesModal
          files={files}
          fileView={fileView}
          onOpen={openFile}
          onBack={() => setFileView(null)}
          onClose={() => setFilesOpen(false)}
        />
      )}
    </div>
  );
}

function MessageView({
  m,
  onRespond,
}: {
  m: ChatMessage;
  onRespond: (id: string, allow: boolean) => void;
}) {
  if (m.kind === "user") {
    return (
      <div className="msg user">
        <div className="bubble">{m.text}</div>
      </div>
    );
  }
  if (m.kind === "assistant") {
    return (
      <div className="msg assistant">
        <div className="bubble">{m.text}</div>
      </div>
    );
  }
  if (m.kind === "tool") {
    return <ToolView m={m} />;
  }
  if (m.kind === "approval") {
    return (
      <div className="msg approval">
        <div className="approval-card">
          <h4>
            Approval required: {m.name} — {m.reason}
          </h4>
          <pre>{JSON.stringify(m.args, null, 2)}</pre>
          {m.resolved ? (
            <div
              style={{
                color: m.resolved === "allowed" ? "var(--completed)" : "var(--failed)",
                fontWeight: 600,
              }}
            >
              {m.resolved === "allowed" ? "Allowed ✓" : "Denied ✗"}
            </div>
          ) : (
            <div className="approval-actions">
              <button className="btn btn-allow" onClick={() => onRespond(m.id, true)}>
                Allow
              </button>
              <button className="btn btn-deny" onClick={() => onRespond(m.id, false)}>
                Deny
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }
  if (m.kind === "system") {
    return (
      <div className="msg system">
        <pre>{m.text}</pre>
      </div>
    );
  }
  return (
    <div className="msg status">
      — {m.status}
      {m.reason ? `: ${m.reason}` : ""} —
    </div>
  );
}

function ToolView({ m }: { m: Extract<ChatMessage, { kind: "tool" }> }) {
  const [open, setOpen] = useState(m.open);
  const argsPreview = JSON.stringify(m.args).slice(0, 120);
  return (
    <div className="msg tool">
      <div className="tool-card">
        <div className="tool-head" onClick={() => setOpen(!open)}>
          <span className="chevron">{open ? "▾" : "▸"}</span>
          <span className="name">{m.name}</span>
          <span className="args">{argsPreview}</span>
          {!m.done && <span style={{ color: "var(--running)" }}>…</span>}
        </div>
        {open && m.output && <div className="tool-body">{m.output}</div>}
      </div>
    </div>
  );
}

function SettingsModal(props: {
  apiBaseUrl: string;
  setApiBaseUrl: (v: string) => void;
  primaryModel: string;
  setPrimaryModel: (v: string) => void;
  reviewerModel: string;
  setReviewerModel: (v: string) => void;
  maxTurns: string;
  setMaxTurns: (v: string) => void;
  workspace: string;
  setWorkspace: (v: string) => void;
  workspaces: string[];
  onNewWorkspace: () => void;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" onClick={props.onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Settings</h2>
        <div className="field">
          <label>API base URL</label>
          <input
            value={props.apiBaseUrl}
            onChange={(e) => props.setApiBaseUrl(e.target.value)}
            placeholder="Leave empty for same origin"
          />
        </div>
        <div className="field">
          <label>Primary agent model</label>
          <select value={props.primaryModel} onChange={(e) => props.setPrimaryModel(e.target.value)}>
            <option value="">Use config default</option>
            {MODEL_OPTIONS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Reviewer model</label>
          <select value={props.reviewerModel} onChange={(e) => props.setReviewerModel(e.target.value)}>
            <option value="">Use config default</option>
            {MODEL_OPTIONS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Max turns</label>
          <input
            type="number"
            min={1}
            value={props.maxTurns}
            onChange={(e) => props.setMaxTurns(e.target.value)}
          />
        </div>
        <div className="field">
          <label>Workspace</label>
          <div className="workspace-select">
            <select value={props.workspace} onChange={(e) => props.setWorkspace(e.target.value)}>
              {props.workspaces.map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
            <button className="btn-icon" onClick={props.onNewWorkspace}>
              + New
            </button>
          </div>
        </div>
        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={props.onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function FilesModal(props: {
  files: WorkspaceFileItem[];
  fileView: { path: string; content: string } | null;
  onOpen: (path: string) => void;
  onBack: () => void;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" onClick={props.onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Workspace Files</h2>
        {props.fileView ? (
          <div className="file-view">
            <button className="btn btn-secondary" onClick={props.onBack} style={{ marginBottom: 8 }}>
              ← Back
            </button>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>{props.fileView.path}</div>
            <pre>{props.fileView.content}</pre>
          </div>
        ) : (
          <>
            {props.files.length === 0 && <p className="empty-hint">No files yet.</p>}
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {props.files.map((f) => (
                <li
                  key={f.path}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    padding: "6px 4px",
                    borderBottom: "1px solid var(--border)",
                    cursor: "pointer",
                  }}
                  onClick={() => props.onOpen(f.path)}
                >
                  <span>{f.path}</span>
                  <span style={{ color: "var(--muted)" }}>{f.size} B</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}
