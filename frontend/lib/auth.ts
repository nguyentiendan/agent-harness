export interface User {
  id: string;
  username: string;
  role: "admin" | "user";
  active: boolean;
  must_change_password: boolean;
}
let csrf = "";
let generation = 0;
export const authGeneration = () => generation;
export function beginAuthChange() {
  generation++;
}
export function clearAuth() {
  generation++;
  csrf = "";
}
export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const started = generation;
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  if (init.method && !["GET", "HEAD"].includes(init.method))
    headers.set("X-CSRF-Token", csrf);
  const res = await fetch(path, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
    headers,
  });
  const data = await res.json().catch(() => ({}));
  if (started !== generation)
    throw new Error("Account changed. Please try again.");
  if (!res.ok) {
    if (res.status === 401) {
      clearAuth();
      window.dispatchEvent(new Event("auth-expired"));
    }
    const message =
      typeof data.detail === "string"
        ? data.detail
        : "Please check the form fields and try again.";
    throw new Error(message);
  }
  if (data.csrf_token) csrf = data.csrf_token;
  return data as T;
}
export const me = () =>
  apiRequest<{ user: User; csrf_token: string }>("/api/auth/me");
