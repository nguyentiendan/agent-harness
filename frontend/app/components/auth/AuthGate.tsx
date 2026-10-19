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
import { UsagePanel } from "../admin/UsagePanel";
import { UsersPanel } from "../admin/UsersPanel";

export default function AuthGate({
  children,
}: {
  children: (user: User) => ReactNode;
}) {
  const changing = useRef(false);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [usageOpen, setUsageOpen] = useState<"mine" | "admin" | null>(null);
  const [usersOpen, setUsersOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  useEffect(() => {
    let live = true;
    const expired = () => {
      if (live) {
        setUser(null);
        setUsersOpen(false);
      setUsageOpen(null);
        setPasswordOpen(false);
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
      setUsersOpen(false);
      setUsageOpen(null);
      setPasswordOpen(false);
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
  return (
    <div className="flex h-dvh flex-col bg-white text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
      <div className="flex flex-wrap items-center justify-end gap-2 border-b border-zinc-200 px-4 py-2 text-xs dark:border-zinc-800">
        <span className="mr-auto font-medium">
          {user.username}{" "}
          <span className="ml-2 rounded-full bg-teal-50 px-2 py-1 text-teal-700 dark:bg-teal-950 dark:text-teal-300">
            {user.role}
          </span>
        </span>
        {error && (
          <span role="alert" className="text-rose-600">
            {error}
          </span>
        )}
        {user.role === "admin" && !user.must_change_password && (
          <button className="ui-button" onClick={() => setUsersOpen(true)}>
            Manage users
          </button>
        )}
        {!user.must_change_password && <button className="ui-button" onClick={() => setUsageOpen("mine")}>My usage</button>}
        {user.role === "admin" && !user.must_change_password && <button className="ui-button" onClick={() => setUsageOpen("admin")}>AI limits</button>}
        <button className="ui-button" onClick={() => setPasswordOpen(true)}>
          Password
        </button>
        <button className="ui-button" disabled={busy} onClick={logout}>
          Sign out
        </button>
      </div>
      <div className="min-h-0 flex-1">
        {!user.must_change_password && children(user)}
      </div>
      {(passwordOpen || user.must_change_password) && (
        <PasswordDialog
          required={user.must_change_password}
          onClose={() => setPasswordOpen(false)}
          onChanged={() => {
            clearAuth();
            setUser(null);
            setPasswordOpen(false);
            setError("Password changed. Sign in again.");
          }}
        />
      )}
      {usageOpen && !user.must_change_password && <UsagePanel key={usageOpen} user={user} admin={usageOpen === "admin" && user.role === "admin"} onClose={() => setUsageOpen(null)} />}
      {usersOpen && (
        <UsersPanel currentId={user.id} onClose={() => setUsersOpen(false)} />
      )}
    </div>
  );
}
function PasswordDialog({
  required,
  onClose,
  onChanged,
}: {
  required: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    beginAuthChange();
    setBusy(true);
    setError("");
    const data = new FormData(e.currentTarget);
    if (data.get("new_password") !== data.get("confirm")) {
      setError("Passwords do not match.");
      setBusy(false);
      return;
    }
    try {
      await apiRequest("/api/auth/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: data.get("current_password"),
          new_password: data.get("new_password"),
        }),
      });
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not change password.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <ModalShell
      title={required ? "Set your own password" : "Change password"}
      description={
        required
          ? "Replace your temporary password before using the platform."
          : "Changing your password signs out all your devices."
      }
      onClose={() => {
        if (!required && !busy) onClose();
      }}
    >
      <form onSubmit={submit} className="space-y-4">
        <label className="block">
          <span className="ui-label">Current password</span>
          <input
            type="password"
            name="current_password"
            autoComplete="current-password"
            required
            className="ui-field"
          />
        </label>
        <label className="block">
          <span className="ui-label">New password (12–128 characters)</span>
          <input
            type="password"
            name="new_password"
            autoComplete="new-password"
            required
            minLength={12}
            maxLength={128}
            className="ui-field"
          />
        </label>
        <label className="block">
          <span className="ui-label">Confirm password</span>
          <input
            type="password"
            name="confirm"
            autoComplete="new-password"
            required
            className="ui-field"
          />
        </label>
        {error && (
          <p role="alert" className="text-sm text-rose-600">
            {error}
          </p>
        )}
        <button className="ui-primary" disabled={busy}>
          {busy ? "Saving…" : "Save password"}
        </button>
      </form>
    </ModalShell>
  );
}
