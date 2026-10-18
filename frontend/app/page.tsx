"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createSession,
  deleteSession,
  getSessionEvents,
  listSessions,
  listWorkspaceFiles,
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
import { Icon, PATHS } from "./components/icons";
import { ModalShell, SelectField } from "./components/controls";
import { SettingsModal, FilesModal } from "./components/workspace-modals";

import AuthGate from "./components/auth/AuthGate";
import type { User } from "../lib/auth";

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

const SUGGESTIONS = [
  "Help me brainstorm a marketing plan",
  "Explain a technical concept simply",
  "Help me write a professional email",
  "Review a code snippet I paste here",
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
  running:
    "bg-amber-100 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300",
  completed:
    "bg-emerald-100 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300",
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

export default function Home() {
  return (
    <AuthGate>{(user) => <ChatHome key={user.id} user={user} />}</AuthGate>
  );
}

function ChatHome({ user }: { user: User }) {
  const apiBaseUrl = "";
  const [allUsers, setAllUsers] = useState(false);
  const [primaryModel, setPrimaryModel] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const selectedRef = useRef("");
  const sendingRef = useRef(false);
  const viewEpoch = useRef(0);
  const pendingSend = useRef<{
    session: string;
    text: string;
    request: string;
    model: string | null;
  } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SessionSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState("");
  const [status, setStatus] = useState<"idle" | HarnessStatus>("idle");

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");

  const [filesOpen, setFilesOpen] = useState(false);
  const [filesLoading, setFilesLoading] = useState(false);
  const [filesError, setFilesError] = useState("");
  const fileRequestRef = useRef(0);
  const [files, setFiles] = useState<WorkspaceFileItem[]>([]);
  const [fileView, setFileView] = useState<{
    path: string;
    content: string;
  } | null>(null);

  const closeStreamRef = useRef<(() => void) | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const refreshSessions = useCallback(async () => {
    try {
      const r = await listSessions(apiBaseUrl, allUsers);
      setSessions(r.sessions);
    } catch {
      // backend not ready
    }
  }, [apiBaseUrl, allUsers]);

  const refreshFiles = useCallback(async () => {
    const sid = selectedRef.current;
    setFilesError("");
    if (!sid) {
      setFiles([]);
      setFilesLoading(false);
      setFilesError("Open or start a session to browse its files.");
      return;
    }
    setFilesLoading(true);
    try {
      const r = await listWorkspaceFiles(apiBaseUrl, sid);
      if (selectedRef.current === sid) setFiles(r.files);
    } catch (e) {
      if (selectedRef.current === sid)
        setFilesError(e instanceof Error ? e.message : "Could not load files.");
    } finally {
      if (selectedRef.current === sid) setFilesLoading(false);
    }
  }, [apiBaseUrl]);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

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
    (ev: HarnessEvent, replay = false) => {
      switch (ev.type) {
        case "user":
          setMessages((m) => [
            ...m,
            { kind: "user", text: String(ev.text ?? "") },
          ]);
          break;
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
          setMessages((m) => [
            ...m,
            { kind: "assistant", text: String(ev.text ?? "") },
          ]);
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
          if (!replay) setStatus(String(ev.status ?? "idle") as HarnessStatus);
          setMessages((m) => [
            ...m,
            {
              kind: "status",
              status: String(ev.status ?? ""),
              reason: String(ev.reason ?? ""),
            },
          ]);
          if (!replay) {
            closeStreamRef.current?.();
            refreshSessions();
            refreshFiles();
          }
          break;
      }
    },
    [refreshSessions, refreshFiles],
  );

  const send = async (override?: string) => {
    const text = (override ?? input).trim();
    if (
      !text ||
      (currentSessionId &&
        sessions.some(
          (s) => s.session_id === currentSessionId && s.owner_id !== user.id,
        ))
    )
      return;
    if (text.startsWith("/")) {
      setInput("");
      await handleCommand(text);
      return;
    }
    if (status === "running" || sendingRef.current) return;
    sendingRef.current = true;
    const epoch = viewEpoch.current;
    let id = currentSessionId;
    selectedRef.current = id;
    setInput("");
    setCurrentSessionId(id);
    setStatus("running");
    closeStreamRef.current?.();
    try {
      if (!id) {
        const created = await createSession();
        if (epoch !== viewEpoch.current) return;
        id = created.session_id;
        selectedRef.current = id;
        setCurrentSessionId(id);
      }
      if (
        !pendingSend.current ||
        pendingSend.current.session !== id ||
        pendingSend.current.text !== text
      )
        pendingSend.current = {
          session: id,
          text,
          request: crypto.randomUUID(),
          model: primaryModel || null,
        };
      const result = await startHarness(apiBaseUrl, {
        session_id: id,
        request_id: pendingSend.current.request,
        goal: text,
        primary_model: pendingSend.current.model,
      });
      pendingSend.current = null;
      if (selectedRef.current !== id) return;
      closeStreamRef.current = streamEvents(
        apiBaseUrl,
        id,
        (ev) => {
          if (selectedRef.current === id) applyEvent(ev);
        },
        result.event_offset,
      );
    } catch (e) {
      if (selectedRef.current !== id) return;
      setInput(text);
      setStatus("failed");
      setMessages((m) => [
        ...m,
        {
          kind: "status",
          status: "failed",
          reason: e instanceof Error ? e.message : String(e),
        },
      ]);
    } finally {
      sendingRef.current = false;
      refreshSessions();
    }
  };

  const newSession = () => {
    viewEpoch.current++;
    pendingSend.current = null;
    selectedRef.current = "";
    setFiles([]);
    setFilesLoading(false);
    setFilesError("");
    fileRequestRef.current++;
    setFileView(null);
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

  const pushSystem = (text: string) =>
    setMessages((m) => [...m, { kind: "system", text }]);

  /** Slash commands: some run locally, the rest are handled by the backend. */
  const handleCommand = async (raw: string) => {
    const trimmed = raw.trim();
    const spaceIdx = trimmed.indexOf(" ");
    const cmd = (
      spaceIdx === -1 ? trimmed.slice(1) : trimmed.slice(1, spaceIdx)
    ).toLowerCase();
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
          pushSystem(
            `Usage: /model <model-name>  (current: ${primaryModel || "config default"})`,
          );
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
          pushSystem(
            `Command failed: ${e instanceof Error ? e.message : String(e)}`,
          );
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
    viewEpoch.current++;
    selectedRef.current = sid;
    setCurrentSessionId(sid);
    setMessages([]);
    setInput("");
    setFiles([]);
    setFilesLoading(false);
    setFilesError("");
    fileRequestRef.current++;
    setFileView(null);
    setStatus("running");
    setSidebarOpen(false);
    closeStreamRef.current?.();
    const rec = sessions.find((s) => s.session_id === sid);
    try {
      const r = await getSessionEvents(apiBaseUrl, sid);
      if (selectedRef.current !== sid) return;
      if (rec && !r.events.some((ev) => ev.type === "user"))
        setMessages([{ kind: "user", text: rec.goal }]);
      for (const ev of r.events) applyEvent(ev, true);
      const latest = await listSessions(apiBaseUrl, allUsers);
      if (selectedRef.current !== sid) return;
      const current = latest.sessions.find((s) => s.session_id === sid);
      setSessions(latest.sessions);
      setStatus((current?.status as HarnessStatus) || "idle");
      if (
        current?.status === "running" ||
        (rec?.status === "running" && r.events.at(-1)?.type !== "status")
      ) {
        closeStreamRef.current = streamEvents(
          apiBaseUrl,
          sid,
          (ev) => {
            if (selectedRef.current === sid) applyEvent(ev);
          },
          r.events.length,
        );
      }
    } catch (e) {
      if (selectedRef.current === sid) {
        setStatus("failed");
        pushSystem(
          `Could not load session: ${e instanceof Error ? e.message : String(e)}`,
        );
      }
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await deleteSession(apiBaseUrl, deleteTarget.session_id);
      if (selectedRef.current === deleteTarget.session_id) newSession();
      setSessions((items) =>
        items.filter((item) => item.session_id !== deleteTarget.session_id),
      );
      setDeleteTarget(null);
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  };

  const openFile = async (path: string) => {
    const sid = selectedRef.current;
    if (!sid) return;
    const request = ++fileRequestRef.current;
    setFilesError("");
    try {
      const r = await readWorkspaceFile(apiBaseUrl, sid, path);
      if (request === fileRequestRef.current && sid === selectedRef.current)
        setFileView({ path: r.path, content: r.content + (r.truncated ? "\n\n[Preview truncated at 1 MiB]" : "") });
    } catch (e) {
      if (request === fileRequestRef.current && sid === selectedRef.current)
        setFilesError(
          e instanceof Error ? e.message : "Could not preview file.",
        );
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const running = status === "running";

  const readOnly =
    !!currentSessionId &&
    sessions.some(
      (s) => s.session_id === currentSessionId && s.owner_id !== user.id,
    );

  const currentMetrics = sessions.find(
    (s) => s.session_id === currentSessionId,
  )?.metrics;

  const sidebar = (
    <div
      className={`h-full flex-col ${sidebarCollapsed ? "flex md:hidden" : "flex"}`}
    >
      <div className="border-b border-zinc-200 p-4 dark:border-zinc-800">
        <div className="mb-4 flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-teal-600 text-white dark:bg-teal-500 dark:text-zinc-950">
            <Icon d={PATHS.terminal} size={17} />
          </span>
          <div className="min-w-0">
            <div className="truncate text-[15px] font-bold tracking-tight text-zinc-900 dark:text-zinc-50">
              AI Task Harness
            </div>
            <div className="text-[11px] text-zinc-500 dark:text-zinc-400">
              Long-running agent tasks
            </div>
          </div>
          <button
            type="button"
            aria-label="Collapse sidebar"
            title="Collapse sidebar"
            aria-expanded={true}
            onClick={() => {
              setSidebarCollapsed(true);
              setSidebarOpen(false);
            }}
            className="ml-auto flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-zinc-500 hover:bg-zinc-200 focus-visible:outline-2 focus-visible:outline-teal-600 dark:hover:bg-zinc-800"
          >
            <Icon d="M3 3h18v18H3zM9 3v18m8-14-3 3 3 3" size={18} />
          </button>
        </div>
        <button
          type="button"
          onClick={newSession}
          className="mb-3 flex w-full items-center justify-center gap-2 rounded-lg bg-teal-600 px-3 py-2 text-[13px] font-semibold text-white transition-colors hover:bg-teal-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-600 dark:bg-teal-500 dark:text-zinc-950 dark:hover:bg-teal-400"
        >
          <Icon d={PATHS.plus} size={15} /> New chat
        </button>
        {user.role === "admin" && (
          <SelectField
            label="Chat visibility"
            value={allUsers ? "all" : "mine"}
            options={[
              { value: "mine", label: "My chats" },
              { value: "all", label: "All users’ chats" },
            ]}
            onChange={(v) => {
              newSession();
              setAllUsers(v === "all");
            }}
          />
        )}
        <p className="mt-2 text-xs text-zinc-500">
          Private conversations · Projects coming next
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        <div className="px-2 pt-2 pb-1 text-[11px] font-semibold tracking-wide text-zinc-400 uppercase dark:text-zinc-500">
          Sessions
        </div>
        {sessions.length === 0 && (
          <p className="px-2 py-3 text-[13px] text-zinc-400 dark:text-zinc-500">
            No chats yet
          </p>
        )}
        {sessions.map((s) => {
          const active = s.session_id === currentSessionId;
          return (
            <div
              key={s.session_id}
              className="group relative flex items-center"
            >
              <button
                type="button"
                onClick={() => loadSession(s.session_id)}
                className={`mb-0.5 min-w-0 w-full rounded-lg pl-3 pr-10 py-2.5 text-left transition-colors ${
                  active
                    ? "bg-white shadow-sm ring-1 ring-zinc-200 dark:bg-zinc-900 dark:ring-zinc-800"
                    : "hover:bg-zinc-200/60 dark:hover:bg-zinc-900"
                }`}
              >
                <div className="truncate text-[13px] font-medium text-zinc-800 dark:text-zinc-100">
                  {s.goal}
                  {allUsers && (
                    <span className="mt-1 block text-xs font-normal text-teal-600">
                      {s.owner_username}
                    </span>
                  )}
                </div>
                <div className="mt-1 flex items-center gap-2 text-[11px] text-zinc-500 dark:text-zinc-400">
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full ${DOT_STYLES[s.status] ?? DOT_STYLES.idle} ${s.status === "running" ? "animate-pulse" : ""}`}
                  />
                  <span className="capitalize">{s.status}</span>
                  <span aria-hidden="true">·</span>
                  <span>{timeAgo(s.updated_at)}</span>
                </div>
              </button>
              <button
                type="button"
                aria-label={`Delete session: ${s.goal}`}
                title="Delete session"
                onClick={() => {
                  setDeleteTarget(s);
                  setDeleteError("");
                }}
                className="absolute right-2 rounded p-1 text-zinc-400 hover:bg-rose-100 hover:text-rose-600 focus-visible:outline-2 dark:hover:bg-rose-950"
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  aria-hidden="true"
                >
                  <path d="M3 6h18M8 6V3h8v3M5 6l1 15h12l1-15M10 10v7M14 10v7" />
                </svg>
              </button>
            </div>
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
    <div className="flex h-full overflow-hidden bg-zinc-100 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
      {sidebarOpen && (
        <button
          type="button"
          aria-label="Close sidebar"
          onClick={() => setSidebarOpen(false)}
          className="fixed inset-0 z-30 bg-zinc-950/50 md:hidden"
        />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-72 shrink-0 -translate-x-full border-r border-zinc-200 bg-zinc-50 transition-transform duration-200 md:static md:translate-x-0 dark:border-zinc-800 dark:bg-zinc-950 ${sidebarCollapsed ? "md:w-16" : "md:w-72"} ${
          sidebarOpen ? "translate-x-0" : ""
        }`}
      >
        {sidebar}
        {sidebarCollapsed && (
          <nav
            aria-label="Sidebar shortcuts"
            className="hidden h-full flex-col items-center gap-2 py-3 md:flex"
          >
            <button
              type="button"
              className="ui-icon-button"
              title="Expand sidebar"
              aria-label="Expand sidebar"
              aria-expanded={false}
              onClick={() => setSidebarCollapsed(false)}
            >
              <Icon d="M3 3h18v18H3zM9 3v18m5-14 3 3-3 3" size={19} />
            </button>
            <span className="my-1 h-px w-8 bg-zinc-200 dark:bg-zinc-800" />
            <button
              type="button"
              className="ui-icon-button bg-teal-600 text-white hover:bg-teal-700 hover:text-white dark:text-white"
              title="New chat"
              aria-label="New chat"
              onClick={newSession}
            >
              <Icon d={PATHS.plus} size={20} />
            </button>
            <div className="flex-1" />
            <button
              type="button"
              className="ui-icon-button"
              title="Files"
              aria-label="Files"
              onClick={() => {
                setFilesOpen(true);
                setFileView(null);
                refreshFiles();
              }}
            >
              <Icon d={PATHS.file} size={19} />
            </button>
            <button
              type="button"
              className="ui-icon-button"
              title="Settings"
              aria-label="Settings"
              onClick={() => setSettingsOpen(true)}
            >
              <Icon d={PATHS.gear} size={19} />
            </button>
            <ThemeToggle />
          </nav>
        )}
      </aside>

      <main className="flex h-full min-w-0 flex-1 flex-col bg-white dark:bg-zinc-950">
        <header className="flex items-center gap-3 border-b border-zinc-200 px-4 py-3 sm:px-5 dark:border-zinc-800">
          <button
            type="button"
            aria-label="Open sidebar"
            onClick={() => {
              setSidebarOpen(true);
              setSidebarCollapsed(false);
            }}
            className={`inline-flex h-9 w-9 items-center justify-center rounded-lg text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-800 md:hidden dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-100`}
          >
            <Icon d={PATHS.menu} size={18} />
          </button>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[14px] font-semibold text-zinc-900 dark:text-zinc-50">
              {sessions.find((s) => s.session_id === currentSessionId)?.goal ||
                "New chat"}
            </div>
          </div>
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] font-semibold capitalize ${STATUS_STYLES[status] ?? STATUS_STYLES.idle}`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${DOT_STYLES[status] ?? DOT_STYLES.idle} ${running ? "animate-pulse" : ""}`}
            />
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
          <div
            className="flex flex-wrap gap-x-4 gap-y-1 border-b border-zinc-200 px-5 py-2 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400"
            aria-label="Run metrics"
          >
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
                  Your conversations are private. Ask a question or work with files in your chat’s offline sandbox.
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
                  <div
                    className="flex items-center gap-1.5 pl-11"
                    aria-label="Agent is working"
                  >
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
                disabled={readOnly}
                placeholder={
                  readOnly
                    ? "Viewing another user’s chat — sending is disabled"
                    : "Type a message or /help…"
                }
                rows={1}
                className="max-h-[180px] min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-[14px] text-zinc-900 placeholder:text-zinc-400 focus:outline-none dark:text-zinc-50 dark:placeholder:text-zinc-500"
              />
              <button
                type="button"
                onClick={() => send()}
                disabled={
                  readOnly ||
                  !input.trim() ||
                  (running && !input.trim().startsWith("/"))
                }
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

      {deleteTarget && (
        <ModalShell
          title="Delete session"
          onClose={() => {
            if (!deleting) setDeleteTarget(null);
          }}
        >
          <p className="mb-3 text-sm">
            Delete “{deleteTarget.goal}” and its conversation history? The chat
            will be hidden immediately and retained for 7 days before cleanup.
          </p>
          {deleteError && (
            <p role="alert" className="mb-3 text-sm text-rose-600">
              {deleteError}
            </p>
          )}
          <div className="flex justify-end gap-3">
            <button
              type="button"
              disabled={deleting}
              onClick={() => setDeleteTarget(null)}
              className="rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={deleting || deleteTarget.status === "running"}
              onClick={confirmDelete}
              className="rounded-lg bg-rose-600 px-3 py-2 text-sm text-white disabled:opacity-40"
            >
              {deleting ? "Deleting…" : "Delete session"}
            </button>
          </div>
          {deleteTarget.status === "running" && (
            <p className="mt-3 text-sm text-amber-600">
              Stop this session before deleting it.
            </p>
          )}
        </ModalShell>
      )}

      {settingsOpen && (
        <SettingsModal
          primaryModel={primaryModel}
          setPrimaryModel={setPrimaryModel}
          onClose={() => setSettingsOpen(false)}
        />
      )}

      {filesOpen && (
        <FilesModal
          workspace="this chat"
          loading={filesLoading}
          error={filesError}
          onRefresh={refreshFiles}
          files={files}
          fileView={fileView}
          onOpen={openFile}
          onBack={() => {
            fileRequestRef.current++;
            setFileView(null);
          }}
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
    return (
      <div className="rounded-xl border border-zinc-200 bg-zinc-50 p-4 dark:border-zinc-800 dark:bg-zinc-900">
        <Markdown text={m.text} />
      </div>
    );
  }
  if (m.kind === "user") {
    return (
      <div className="flex animate-message-in justify-end gap-3">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-teal-600 px-4 py-2.5 text-white sm:max-w-[75%] dark:bg-teal-500 dark:text-zinc-950">
          <p className="text-[14px] leading-relaxed break-words whitespace-pre-wrap">
            {m.text}
          </p>
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
                {m.reason ? (
                  <span className="font-normal"> — {m.reason}</span>
                ) : null}
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
                  <Icon
                    d={m.resolved === "allowed" ? PATHS.check : PATHS.x}
                    size={14}
                  />
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
          <span className="text-[10px] text-zinc-400 dark:text-zinc-500">
            {open ? "▾" : "▸"}
          </span>
          <span className="shrink-0 font-mono text-[12px] font-semibold text-teal-700 dark:text-teal-300">
            {m.name}
          </span>
          <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-zinc-400 dark:text-zinc-500">
            {argsPreview}
          </span>
          {!m.done && (
            <span
              className="flex shrink-0 items-center gap-1"
              aria-label="Running"
            >
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
