import { apiRequest } from "./auth";
export type HarnessStatus = "running" | "completed" | "failed" | "stopped";

export interface RunMetrics {
  status?: string;
  duration_seconds?: number;
  turns?: number;
  tool_calls?: number;
  tools?: Record<string, number>;
  approvals?: number;
  model?: string;
}

export interface SessionSummary {
  session_id: string;
  goal: string;
  status: HarnessStatus | string;
  workspace: string;
  owner_id: string;
  owner_username: string;
  created_at: number;
  updated_at: number;
  result_text?: string;
  error?: string;
  metrics?: RunMetrics;
}

export interface HarnessEvent {
  type:
    | "user"
    | "tool_call"
    | "tool_result"
    | "assistant"
    | "approval"
    | "approval_result"
    | "status";
  [key: string]: unknown;
}

export interface ApprovalItem {
  approval_id: string;
  session_id: string;
  name: string;
  args: Record<string, unknown>;
  reason: string;
  created_at: number;
}

export interface WorkspaceFileItem {
  path: string;
  size: number;
}

export function buildUrl(baseUrl: string, path: string): string {
  const base = baseUrl.trim().replace(/\/+$/, "");
  return `${base}${path}`;
}

async function request<T>(
  _baseUrl: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  return apiRequest<T>(path, init);
}
export const createSession = () =>
  apiRequest<SessionSummary>("/api/sessions", { method: "POST", body: "{}" });

// Config / workspaces / sessions
export const fetchConfig = (baseUrl: string) =>
  request<{ config: Record<string, unknown>; config_path: string }>(
    baseUrl,
    "/api/config",
  );

export const listSessions = (baseUrl: string, allUsers = false) =>
  request<{ sessions: SessionSummary[] }>(
    baseUrl,
    `/api/sessions?all_users=${allUsers}`,
  );

export const getSessionEvents = (baseUrl: string, sessionId: string) =>
  request<{ session_id: string; events: HarnessEvent[] }>(
    baseUrl,
    `/api/sessions/${sessionId}`,
  );

// Harness control
export const startHarness = (
  baseUrl: string,
  payload: {
    session_id: string;
    request_id: string;
    goal: string;
    max_turns?: number | null;
    primary_model?: string | null;
    reviewer_model?: string | null;
  },
) =>
  request<{
    message: string;
    session_id: string;
    workspace: string;
    event_offset: number;
  }>(baseUrl, "/api/harness/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

export const stopHarness = (baseUrl: string, sessionId: string) =>
  request<{ message: string; session_id: string; status: string }>(
    baseUrl,
    `/api/harness/stop/${sessionId}`,
    { method: "POST" },
  );

export const fetchStatus = (baseUrl: string, sessionId: string) =>
  request<Record<string, unknown>>(baseUrl, `/api/harness/status/${sessionId}`);

export const listWorkspaceFiles = (baseUrl: string, sessionId: string) =>
  request<{ session_id: string; files: WorkspaceFileItem[] }>(
    baseUrl,
    `/api/harness/list/${sessionId}`,
  );

export const readWorkspaceFile = (
  baseUrl: string,
  sessionId: string,
  path: string,
) =>
  request<{ path: string; content: string; truncated?: boolean }>(
    baseUrl,
    `/api/harness/read/${sessionId}?path=${encodeURIComponent(path)}`,
  );

export const listApprovals = (baseUrl: string, sessionId: string) =>
  request<{ approvals: ApprovalItem[] }>(
    baseUrl,
    `/api/harness/approvals/${sessionId}`,
  );

export const runCommand = (
  baseUrl: string,
  sessionId: string,
  command: string,
  args = "",
) =>
  request<{ output: string }>(baseUrl, "/api/command", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId || null, command, args }),
  });

export const resolveApproval = (
  baseUrl: string,
  approvalId: string,
  allow: boolean,
) =>
  request<{ approval_id: string; allowed: boolean }>(
    baseUrl,
    `/api/harness/approve?approval_id=${encodeURIComponent(approvalId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ allow }),
    },
  );

/**
 * Subscribe to structured events via SSE. Returns a close function.
 */
export function streamEvents(
  baseUrl: string,
  sessionId: string,
  onEvent: (event: HarnessEvent) => void,
  after = 0,
): () => void {
  const url = buildUrl(
    baseUrl,
    `/api/harness/stream/${sessionId}?after=${after}`,
  );
  const es = new EventSource(url);

  es.onerror = () => {
    void apiRequest("/api/auth/me").catch(() => es.close());
  };
  es.onmessage = (msg) => {
    try {
      const data = JSON.parse(msg.data);
      if (data && typeof data.type === "string") onEvent(data as HarnessEvent);
    } catch {
      // ignore malformed events
    }
  };

  return () => es.close();
}

export const deleteSession = (baseUrl: string, sessionId: string) =>
  request<{ message: string; session_id: string }>(
    baseUrl,
    `/api/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
