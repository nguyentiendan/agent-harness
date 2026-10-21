"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  apiRequest,
  authGeneration,
  beginAuthChange,
  clearAuth,
  me,
  type User,
} from "../../../lib/auth";
import { ModalShell } from "../controls";
import { AccountMenu } from "./AccountMenu";
import { SettingsDialog } from "./SettingsDialog";
import { PasswordDialog } from "./PasswordDialog";

export default function AuthGate({
  children,
}: {
  children: (
    user: User,
    accountMenu: (compact?: boolean) => ReactNode,
  ) => ReactNode;
}) {
  const changing = useRef(false);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [logoutOpen, setLogoutOpen] = useState(false);
  useEffect(() => {
    let live = true;
    const expired = () => {
      if (live) {
        setUser(null);
        setSettingsOpen(false);
        setLogoutOpen(false);
      }
    };
    window.addEventListener("auth-expired", expired);
    const check = async () => {
      if (changing.current) return;
      const epoch = authGeneration();
      try {
        const r = await me();
        if (live && epoch === authGeneration()) setUser(r.user);
      } catch {
        /* 401 handled centrally; network failures do not log out */
      } finally {
        if (live) setLoading(false);
      }
    };
    check();
    const timer = setInterval(check, 5000);
    return () => {
      live = false;
      clearInterval(timer);
      window.removeEventListener("auth-expired", expired);
    };
  }, []);
  const login = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    changing.current = true;
    beginAuthChange();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      const r = await apiRequest<{ user: User }>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(data)),
      });
      setUser(r.user);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not sign in.");
    } finally {
      changing.current = false;
      setBusy(false);
    }
  };
  const logout = async () => {
    changing.current = true;
    beginAuthChange();
    setBusy(true);
    setError("");
    try {
      await apiRequest("/api/auth/logout", { method: "POST" });
      clearAuth();
      setUser(null);
      setSettingsOpen(false);
      setLogoutOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not sign out.");
    } finally {
      changing.current = false;
      setBusy(false);
    }
  };
  if (loading)
    return (
      <div className="grid min-h-dvh place-items-center bg-zinc-50 text-zinc-500 dark:bg-zinc-950">
        Loading your account…
      </div>
    );
  if (!user)
    return (
      <main className="grid min-h-dvh place-items-center bg-zinc-50 p-6 dark:bg-zinc-950">
        <div className="w-full max-w-md rounded-3xl border border-zinc-200 bg-white p-8 shadow-xl dark:border-zinc-800 dark:bg-zinc-900">
          <div className="mb-6 inline-flex rounded-xl bg-teal-600 px-3 py-2 font-mono font-bold text-white">
            &gt;_
          </div>
          <h1 className="text-2xl font-semibold">Welcome back</h1>
          <p className="mb-7 mt-2 text-sm text-zinc-500">
            Sign in to your company AI platform.
          </p>
          <form onSubmit={login} className="space-y-5">
            <label className="block">
              <span className="ui-label">Username</span>
              <input
                name="username"
                autoComplete="username"
                required
                maxLength={80}
                className="ui-field"
                autoFocus
              />
            </label>
            <label className="block">
              <span className="ui-label">Password</span>
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
                maxLength={128}
                className="ui-field"
              />
            </label>
            {error && (
              <p role="alert" className="text-sm text-rose-600">
                {error}
              </p>
            )}
            <button
              className="ui-primary w-full justify-center"
              disabled={busy}
            >
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
          <p className="mt-6 text-xs text-zinc-500">
            Need an account or password reset? Contact your administrator.
          </p>
        </div>
      </main>
    );
  const changedPassword = () => {
    clearAuth();
    setUser(null);
    setSettingsOpen(false);
    setError("Password changed. Sign in again.");
  };
  const requestLogout = () => {
    setError("");
    setLogoutOpen(true);
  };
  return (
    <div className="flex h-dvh flex-col bg-white text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
      <div className="min-h-0 flex-1">
        {!user.must_change_password &&
          children(user, (compact = false) => (
            <AccountMenu
              user={user}
              compact={compact}
              onSettings={() => setSettingsOpen(true)}
              onLogout={requestLogout}
            />
          ))}
      </div>
      {user.must_change_password && (
        <PasswordDialog
          required
          onClose={() => {}}
          onChanged={changedPassword}
          onLogout={requestLogout}
        />
      )}
      {settingsOpen && !user.must_change_password && (
        <SettingsDialog
          key={user.role}
          user={user}
          onClose={() => setSettingsOpen(false)}
          onLogout={requestLogout}
          onPasswordChanged={changedPassword}
        />
      )}
      {logoutOpen && (
        <ModalShell
          title="Log out?"
          description={`You’re signed in as ${user.username}. You can sign back in to continue your conversations.`}
          onClose={() => {
            if (!busy) setLogoutOpen(false);
          }}
        >
          {error && (
            <p role="alert" className="mb-4 text-sm text-rose-600">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-3">
            <button
              autoFocus
              className="ui-button"
              disabled={busy}
              onClick={() => setLogoutOpen(false)}
            >
              Cancel
            </button>
            <button className="ui-primary" disabled={busy} onClick={logout}>
              {busy ? "Logging out…" : "Log out"}
            </button>
          </div>
        </ModalShell>
      )}
    </div>
  );
}
