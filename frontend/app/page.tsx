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
import Markdown from "./markdown";
import ThemeToggle from "./theme-toggle";

type ChatMessage =
  | { kind: "system"; text: string }
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
  | { kind: "status"; status: string; reason?: string };

const MODEL_OPTIONS = [
  "gpt-4o-mini",
  "gpt-4o",
  "claude-3-7-sonnet-20250219",
  "deepseek/deepseek-chat",
  "deepseek/deepseek-reasoner",
  "gemini/gemini-1.5-pro",
  "ollama/llama3.3",
];

const SUGGESTIONS = [
  "Summarize the files in this workspace",
  "Find and fix the failing tests",
  "Explain how this codebase is structured",
  "Review the latest changes for bugs",
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

const STATUS_STYLES: Record<string, string> = {
  idle: "bg-zinc-200 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
  running: "bg-amber-100 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300",
  completed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300",
  failed: "bg-rose-100 text-rose-700 dark:bg-rose-400/10 dark:text-rose-300",
  stopped: "bg-zinc-200 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400",
};

const DOT_STYLES: Record<string, string> = {
  running: "bg-amber-500",
  completed: "bg-emerald-500",
  failed: "bg-rose-500",
  stopped: "bg-zinc-400",
  idle: "bg-zinc-400",
};

function Icon({ d, size = 16 }: { d: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  );
}

const PATHS = {
  plus: "M5 12h14M12 5v14",
  gear: "M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  folder:
    "M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z",
  panel: "M3 3h18v18H3zM9 3v18",
  menu: "M4 6h16M4 12h16M4 18h16",
  x: "M18 6 6 18M6 6l12 12",
  send: "M12 19V5M5 12l7-7 7 7",
  terminal: "m4 17 6-6-6-6M12 19h8",
  file: "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z M14 2v4a2 2 0 0 0 2 2h4",
  check: "M20 6 9 17l-5-5",
};

export default function Home() {
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [primaryModel, setPrimaryModel] = useState("");
  const [reviewerModel, setReviewerModel] = useState("");
  const [maxTurns, setMaxTurns] = useState("5");
  const [workspace, setWorkspace] = useState("default");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

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
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

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

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [input]);

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

  const send = async (override?: string) => {
    const text = (override ?? input).trim();
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

  const newSession = () => {
    setInput("");
    closeStreamRef.current?.();
    setMessages([]);
    setCurrentSessionId("");
    setStatus("idle");
    setSidebarOpen(false);
    textareaRef.current?.focus();
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
        newSession();
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
    setSidebarOpen(false);
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

  const running = status === "running";

  const currentMetrics = sessions.find((s) => s.session_id === currentSessionId)?.metrics;

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="border-b border-zinc-200 p-4 dark:border-zinc-800">
        <div className="mb-4 flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-teal-600 text-white dark:bg-teal-500 dark:text-zinc-950">
            <Icon d={PATHS.terminal} size={17} />
          </span>
          <div className="min-w-0">
            <div className="truncate text-[15px] font-bold tracking-tight text-zinc-900 dark:text-zinc-50">
              AI Task Harness
            </div>
            <div className="text-[11px] text-zinc-500 dark:text-zinc-400">Long-running agent tasks</div>
          </div>
          <button type="button" aria-label="Collapse sidebar" title="Collapse sidebar"
            onClick={() => { setSidebarCollapsed(true); setSidebarOpen(false); }}
            className="ml-auto flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-zinc-500 hover:bg-zinc-200 focus-visible:outline-2 focus-visible:outline-teal-600 dark:hover:bg-zinc-800">
            <Icon d={PATHS.panel} size={18} />
          </button>
        </div>
        <button
          type="button"
          onClick={newSession}
          className="mb-3 flex w-full items-center justify-center gap-2 rounded-lg bg-teal-600 px-3 py-2 text-[13px] font-semibold text-white transition-colors hover:bg-teal-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-600 dark:bg-teal-500 dark:text-zinc-950 dark:hover:bg-teal-400"
        >
          <Icon d={PATHS.plus} size={15} /> New session
        </button>
        <label className="mb-1.5 block text-[11px] font-semibold tracking-wide text-zinc-500 uppercase dark:text-zinc-400">
          Workspace
        </label>
        <div className="flex gap-2">
          <select
            value={workspace}
            onChange={(e) => setWorkspace(e.target.value)}
            className="min-w-0 flex-1 rounded-lg border border-zinc-200 bg-white px-2.5 py-2 text-[13px] text-zinc-800 transition-colors focus:border-teal-600 focus:ring-2 focus:ring-teal-600/20 focus:outline-none dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100 dark:focus:border-teal-400"
          >
            {workspaces.map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={newWorkspace}
            className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-zinc-200 bg-white px-2.5 py-2 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            <Icon d={PATHS.plus} size={14} /> New
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        <div className="px-2 pt-2 pb-1 text-[11px] font-semibold tracking-wide text-zinc-400 uppercase dark:text-zinc-500">
          Sessions
        </div>
        {sessions.length === 0 && (
          <p className="px-2 py-3 text-[13px] text-zinc-400 dark:text-zinc-500">No sessions yet</p>
        )}
        {sessions.map((s) => {
          const active = s.session_id === currentSessionId;
          return (
            <button
              key={s.session_id}
              type="button"
              onClick={() => loadSession(s.session_id)}
              className={`mb-0.5 w-full rounded-lg px-3 py-2.5 text-left transition-colors ${
                active
                  ? "bg-white shadow-sm ring-1 ring-zinc-200 dark:bg-zinc-900 dark:ring-zinc-800"
                  : "hover:bg-zinc-200/60 dark:hover:bg-zinc-900"
              }`}
            >
              <div className="truncate text-[13px] font-medium text-zinc-800 dark:text-zinc-100">{s.goal}</div>
              <div className="mt-1 flex items-center gap-2 text-[11px] text-zinc-500 dark:text-zinc-400">
                <span className={`h-2 w-2 shrink-0 rounded-full ${DOT_STYLES[s.status] ?? DOT_STYLES.idle} ${s.status === "running" ? "animate-pulse" : ""}`} />
                <span className="capitalize">{s.status}</span>
                <span aria-hidden="true">·</span>
                <span>{timeAgo(s.updated_at)}</span>
              </div>
            </button>
          );
        })}
      </div>

      <div className="flex items-center gap-2 border-t border-zinc-200 p-3 dark:border-zinc-800">
        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 py-2 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
        >
          <Icon d={PATHS.gear} size={15} /> Settings
        </button>
        <button
          type="button"
          onClick={() => {
            setFilesOpen(true);
            setFileView(null);
            refreshFiles();
          }}
          className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 py-2 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
        >
          <Icon d={PATHS.folder} size={15} /> Files
        </button>
        <ThemeToggle />
      </div>
    </div>
  );

  return (
    <div className="flex h-dvh overflow-hidden bg-zinc-100 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
      {sidebarOpen && (
        <button
          type="button"
          aria-label="Close sidebar"
          onClick={() => setSidebarOpen(false)}
          className="fixed inset-0 z-30 bg-zinc-950/50 md:hidden"
        />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-72 shrink-0 -translate-x-full border-r border-zinc-200 bg-zinc-50 transition-transform duration-200 md:static md:translate-x-0 dark:border-zinc-800 dark:bg-zinc-950 ${sidebarCollapsed ? "md:hidden" : ""} ${
          sidebarOpen ? "translate-x-0" : ""
        }`}
      >
        {sidebar}
      </aside>

      <main className="flex h-full min-w-0 flex-1 flex-col bg-white dark:bg-zinc-950">
        <header className="flex items-center gap-3 border-b border-zinc-200 px-4 py-3 sm:px-5 dark:border-zinc-800">
          <button
            type="button"
            aria-label="Open sidebar"
            onClick={() => { setSidebarOpen(true); setSidebarCollapsed(false); }}
            className={`inline-flex h-9 w-9 items-center justify-center rounded-lg text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-800 ${sidebarCollapsed ? "" : "md:hidden"} dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-100`}
          >
            <Icon d={PATHS.menu} size={18} />
          </button>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[14px] font-semibold text-zinc-900 dark:text-zinc-50">
              {workspace}
              <span className="font-normal text-zinc-400 dark:text-zinc-500"> workspace</span>
            </div>
          </div>
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] font-semibold capitalize ${STATUS_STYLES[status] ?? STATUS_STYLES.idle}`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${DOT_STYLES[status] ?? DOT_STYLES.idle} ${running ? "animate-pulse" : ""}`} />
            {status}
          </span>
          {running && (
            <button
              type="button"
              onClick={stopRun}
              className="rounded-lg bg-rose-600 px-3 py-1.5 text-[13px] font-semibold text-white transition-colors hover:bg-rose-700"
            >
              Stop
            </button>
          )}
          <div className="hidden sm:block">
            <ThemeToggle />
          </div>
        </header>
        {currentMetrics && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 border-b border-zinc-200 px-5 py-2 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400" aria-label="Run metrics">
            <span>{currentMetrics.duration_seconds ?? 0}s</span>
            <span>{currentMetrics.tool_calls ?? 0} tool calls</span>
            <span>{currentMetrics.turns ?? 0} turns</span>
            {currentMetrics.model && <span>{currentMetrics.model}</span>}
          </div>
        )}

        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 py-6 sm:px-6">
            {messages.length === 0 ? (
              <div className="flex flex-1 flex-col items-center justify-center py-10 text-center">
                <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-teal-600/10 text-teal-700 dark:bg-teal-400/10 dark:text-teal-300">
                  <Icon d={PATHS.terminal} size={22} />
                </span>
                <h2 className="text-xl font-bold tracking-tight text-zinc-900 dark:text-zinc-50">
                  What should the agent do?
                </h2>
                <p className="mt-1.5 max-w-md text-[13px] text-zinc-500 dark:text-zinc-400">
                  Send a goal and the agent will work in the &ldquo;{workspace}&rdquo; workspace,
                  asking for approval before destructive actions.
                </p>
                <div className="mt-6 grid w-full max-w-lg gap-2 sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => send(s)}
                      className="rounded-lg border border-zinc-200 bg-white px-3.5 py-3 text-left text-[13px] font-medium text-zinc-700 transition-colors hover:border-teal-600/40 hover:bg-teal-600/5 hover:text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:border-teal-400/40 dark:hover:bg-teal-400/5 dark:hover:text-zinc-50"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-5">
                {messages.map((m, i) => (
                  <MessageView key={i} m={m} onRespond={respondApproval} />
                ))}
                {running && (
                  <div className="flex items-center gap-1.5 pl-11" aria-label="Agent is working">
                    {[0, 1, 2].map((d) => (
                      <span
                        key={d}
                        className="h-2 w-2 animate-typing-dot rounded-full bg-zinc-400 dark:bg-zinc-500"
                        style={{ animationDelay: `${d * 0.18}s` }}
                      />
                    ))}
                  </div>
                )}
                <div ref={endRef} />
              </div>
            )}
          </div>
        </div>

        <div className="px-4 pb-4 sm:px-6">
          <div className="mx-auto w-full max-w-3xl">
            <div className="flex items-end gap-2 rounded-xl border border-zinc-300 bg-white p-2 shadow-sm transition-colors focus-within:border-teal-600 focus-within:ring-2 focus-within:ring-teal-600/20 dark:border-zinc-700 dark:bg-zinc-900 dark:focus-within:border-teal-400 dark:focus-within:ring-teal-400/20">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Type a message or /help…"
                rows={1}
                className="max-h-[180px] min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-[14px] text-zinc-900 placeholder:text-zinc-400 focus:outline-none dark:text-zinc-50 dark:placeholder:text-zinc-500"
              />
              <button
                type="button"
                onClick={() => send()}
                disabled={!input.trim() || (running && !input.trim().startsWith("/"))}
                aria-label="Send goal"
                className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-teal-600 text-white transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-zinc-200 disabled:text-zinc-400 dark:bg-teal-500 dark:text-zinc-950 dark:hover:bg-teal-400 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-600"
              >
                <Icon d={PATHS.send} size={16} />
              </button>
            </div>
            <p className="mt-2 text-center text-[11px] text-zinc-400 dark:text-zinc-500">
              Enter to send · Shift+Enter for a new line
            </p>
          </div>
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

function Avatar({ label, teal }: { label: string; teal?: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${
        teal
          ? "bg-teal-600 text-white dark:bg-teal-500 dark:text-zinc-950"
          : "bg-zinc-800 text-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
      }`}
    >
      {label}
    </span>
  );
}

function MessageView({
  m,
  onRespond,
}: {
  m: ChatMessage;
  onRespond: (id: string, allow: boolean) => void;
}) {
  if (m.kind === "system") {
    return <div className="rounded-xl border border-zinc-200 bg-zinc-50 p-4 dark:border-zinc-800 dark:bg-zinc-900"><Markdown text={m.text} /></div>;
  }
  if (m.kind === "user") {
    return (
      <div className="flex animate-message-in justify-end gap-3">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-teal-600 px-4 py-2.5 text-white sm:max-w-[75%] dark:bg-teal-500 dark:text-zinc-950">
          <p className="text-[14px] leading-relaxed break-words whitespace-pre-wrap">{m.text}</p>
        </div>
        <Avatar label="You" teal />
      </div>
    );
  }
  if (m.kind === "assistant") {
    return (
      <div className="flex animate-message-in gap-3">
        <Avatar label="AI" />
        <div className="min-w-0 flex-1 pt-1 text-zinc-700 dark:text-zinc-200">
          <Markdown text={m.text} />
        </div>
      </div>
    );
  }
  if (m.kind === "tool") {
    return <ToolView m={m} />;
  }
  if (m.kind === "approval") {
    return (
      <div className="animate-message-in pl-11">
        <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-400/30 dark:bg-amber-400/10">
          <div className="flex items-start gap-2.5">
            <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-500/20 text-[13px] font-bold text-amber-700 dark:text-amber-300">
              !
            </span>
            <div className="min-w-0 flex-1">
              <h4 className="text-[13px] font-semibold text-amber-900 dark:text-amber-200">
                Approval required: {m.name}
                {m.reason ? <span className="font-normal"> — {m.reason}</span> : null}
              </h4>
              <pre className="mt-2 overflow-x-auto rounded-lg bg-white p-2.5 font-mono text-[12px] text-zinc-700 ring-1 ring-amber-200 dark:bg-zinc-950 dark:text-zinc-300 dark:ring-amber-400/20">
                {JSON.stringify(m.args, null, 2)}
              </pre>
              {m.resolved ? (
                <p
                  className={`mt-2.5 flex items-center gap-1.5 text-[13px] font-semibold ${
                    m.resolved === "allowed"
                      ? "text-emerald-700 dark:text-emerald-300"
                      : "text-rose-700 dark:text-rose-300"
                  }`}
                >
                  <Icon d={m.resolved === "allowed" ? PATHS.check : PATHS.x} size={14} />
                  {m.resolved === "allowed" ? "Allowed" : "Denied"}
                </p>
              ) : (
                <div className="mt-3 flex gap-2">
                  <button
                    type="button"
                    onClick={() => onRespond(m.id, true)}
                    className="rounded-lg bg-emerald-600 px-3.5 py-1.5 text-[13px] font-semibold text-white transition-colors hover:bg-emerald-700"
                  >
                    Allow
                  </button>
                  <button
                    type="button"
                    onClick={() => onRespond(m.id, false)}
                    className="rounded-lg bg-rose-600 px-3.5 py-1.5 text-[13px] font-semibold text-white transition-colors hover:bg-rose-700"
                  >
                    Deny
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="flex animate-message-in items-center gap-3 text-[12px] text-zinc-400 dark:text-zinc-500">
      <span className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" />
      <span>
        {m.status}
        {m.reason ? `: ${m.reason}` : ""}
      </span>
      <span className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" />
    </div>
  );
}

function ToolView({ m }: { m: Extract<ChatMessage, { kind: "tool" }> }) {
  const [open, setOpen] = useState(m.open);
  const argsPreview = JSON.stringify(m.args).slice(0, 120);
  return (
    <div className="animate-message-in pl-11">
      <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
        >
          <span className="text-[10px] text-zinc-400 dark:text-zinc-500">{open ? "▾" : "▸"}</span>
          <span className="shrink-0 font-mono text-[12px] font-semibold text-teal-700 dark:text-teal-300">
            {m.name}
          </span>
          <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-zinc-400 dark:text-zinc-500">
            {argsPreview}
          </span>
          {!m.done && (
            <span className="flex shrink-0 items-center gap-1" aria-label="Running">
              {[0, 1, 2].map((d) => (
                <span
                  key={d}
                  className="h-1.5 w-1.5 animate-typing-dot rounded-full bg-amber-500"
                  style={{ animationDelay: `${d * 0.18}s` }}
                />
              ))}
            </span>
          )}
          {m.done && (
            <span className="shrink-0 text-emerald-600 dark:text-emerald-400">
              <Icon d={PATHS.check} size={14} />
            </span>
          )}
        </button>
        {open && m.output && (
          <pre className="max-h-[300px] overflow-y-auto bg-zinc-950 p-3 font-mono text-[12px] leading-relaxed break-words whitespace-pre-wrap text-zinc-200">
            {m.output}
          </pre>
        )}
      </div>
    </div>
  );
}

const inputCls =
  "w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-[13px] text-zinc-900 transition-colors focus:border-teal-600 focus:ring-2 focus:ring-teal-600/20 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:focus:border-teal-400 dark:focus:ring-teal-400/20";
const labelCls = "mb-1.5 block text-[12px] font-semibold text-zinc-500 dark:text-zinc-400";

function ModalShell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-950/50 p-4 backdrop-blur-[2px]"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className="max-h-[86vh] w-full max-w-[520px] overflow-y-auto rounded-xl bg-white p-5 shadow-2xl ring-1 ring-zinc-950/5 dark:bg-zinc-900 dark:ring-white/10"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-[16px] font-bold tracking-tight text-zinc-900 dark:text-zinc-50">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
          >
            <Icon d={PATHS.x} size={16} />
          </button>
        </div>
        {children}
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
    <ModalShell title="Settings" onClose={props.onClose}>
      <div className="space-y-4">
        <div>
          <label className={labelCls} htmlFor="setting-api-url">API base URL</label>
          <input
            id="setting-api-url"
            className={inputCls}
            value={props.apiBaseUrl}
            onChange={(e) => props.setApiBaseUrl(e.target.value)}
            placeholder="Leave empty for same origin"
          />
        </div>
        <div>
          <label className={labelCls} htmlFor="setting-primary-model">Primary agent model</label>
          <select
            id="setting-primary-model"
            className={inputCls}
            value={props.primaryModel}
            onChange={(e) => props.setPrimaryModel(e.target.value)}
          >
            <option value="">Use config default</option>
            {MODEL_OPTIONS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={labelCls} htmlFor="setting-reviewer-model">Reviewer model</label>
          <select
            id="setting-reviewer-model"
            className={inputCls}
            value={props.reviewerModel}
            onChange={(e) => props.setReviewerModel(e.target.value)}
          >
            <option value="">Use config default</option>
            {MODEL_OPTIONS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={labelCls} htmlFor="setting-max-turns">Max turns</label>
          <input
            id="setting-max-turns"
            className={inputCls}
            type="number"
            min={1}
            value={props.maxTurns}
            onChange={(e) => props.setMaxTurns(e.target.value)}
          />
        </div>
        <div>
          <label className={labelCls} htmlFor="setting-workspace">Workspace</label>
          <div className="flex gap-2">
            <select
              id="setting-workspace"
              className={inputCls}
              value={props.workspace}
              onChange={(e) => props.setWorkspace(e.target.value)}
            >
              {props.workspaces.map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={props.onNewWorkspace}
              className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              <Icon d={PATHS.plus} size={14} /> New
            </button>
          </div>
        </div>
      </div>
      <div className="mt-5 flex justify-end">
        <button
          type="button"
          onClick={props.onClose}
          className="rounded-lg bg-zinc-900 px-4 py-2 text-[13px] font-semibold text-white transition-colors hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
        >
          Done
        </button>
      </div>
    </ModalShell>
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
    <ModalShell title="Workspace Files" onClose={props.onClose}>
      {props.fileView ? (
        <div>
          <button
            type="button"
            onClick={props.onBack}
            className="mb-3 rounded-lg bg-zinc-100 px-3 py-1.5 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
          >
            ← Back
          </button>
          <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold break-all text-zinc-800 dark:text-zinc-100">
            <Icon d={PATHS.file} size={15} /> {props.fileView.path}
          </div>
          <pre className="max-h-[60vh] overflow-y-auto rounded-lg bg-zinc-950 p-3 font-mono text-[12.5px] leading-relaxed break-words whitespace-pre-wrap text-zinc-100">
            {props.fileView.content}
          </pre>
        </div>
      ) : props.files.length === 0 ? (
        <p className="py-6 text-center text-[13px] text-zinc-400 dark:text-zinc-500">No files yet.</p>
      ) : (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {props.files.map((f) => (
            <li key={f.path}>
              <button
                type="button"
                onClick={() => props.onOpen(f.path)}
                className="flex w-full items-center gap-2.5 px-1 py-2.5 text-left transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
              >
                <span className="shrink-0 text-zinc-400 dark:text-zinc-500">
                  <Icon d={PATHS.file} size={15} />
                </span>
                <span className="min-w-0 flex-1 truncate text-[13px] text-zinc-700 dark:text-zinc-200">{f.path}</span>
                <span className="shrink-0 text-[12px] text-zinc-400 tabular-nums dark:text-zinc-500">{f.size} B</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </ModalShell>
  );
}
