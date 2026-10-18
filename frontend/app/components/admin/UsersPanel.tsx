"use client";
import { useEffect, useState } from "react";
import { apiRequest, type User } from "../../../lib/auth";
import { ModalShell, SelectField } from "../controls";
export function UsersPanel({
  currentId,
  onClose,
}: {
  currentId: string;
  onClose: () => void;
}) {
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [role, setRole] = useState("user");
  const [reset, setReset] = useState<User | null>(null);
  const [notice, setNotice] = useState("");
  const refresh = async () => {
    const r = await apiRequest<{ users: User[] }>("/api/admin/users");
    setUsers(r.users);
  };
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, []);
  const action = async (fn: () => Promise<void>) => {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed.");
    } finally {
      setBusy(false);
    }
  };
  const create = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);
    void action(async () => {
      await apiRequest("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({
          username: data.get("username"),
          password: data.get("password"),
          role,
        }),
      });
      form.reset();
      setNotice(
        "Account created. Share the temporary password with the user; they must change it on first sign-in.",
      );
    });
  };
  return (
    <ModalShell
      wide
      title="User management"
      description="Create accounts, manage roles and revoke access."
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <div className="space-y-6">
        {error && (
          <p
            role="alert"
            className="rounded-xl bg-rose-50 p-3 text-sm text-rose-700"
          >
            {error}
          </p>
        )}
        {notice && (
          <p
            role="status"
            className="rounded-xl bg-teal-50 p-3 text-sm text-teal-800"
          >
            {notice}
          </p>
        )}
        <form
          onSubmit={create}
          className="grid gap-3 rounded-xl border border-zinc-200 p-4 sm:grid-cols-2 dark:border-zinc-700"
        >
          <h3 className="font-semibold sm:col-span-2">Add user</h3>
          <label>
            <span className="ui-label">Username</span>
            <input
              className="ui-field"
              name="username"
              required
              minLength={3}
              maxLength={80}
              autoComplete="off"
            />
          </label>
          <label>
            <span className="ui-label">Temporary password</span>
            <input
              className="ui-field"
              name="password"
              type="password"
              required
              minLength={12}
              maxLength={128}
              autoComplete="new-password"
            />
          </label>
          <div>
            <span className="ui-label">Role</span>
            <SelectField
              label="New user role"
              value={role}
              onChange={setRole}
              options={[
                { value: "user", label: "User" },
                { value: "admin", label: "Admin" },
              ]}
            />
          </div>
          <button
            className="ui-primary self-end justify-center"
            disabled={busy}
          >
            Create account
          </button>
        </form>
        <div className="space-y-3">
          {users.map((u) => (
            <div
              key={u.id}
              className="flex flex-wrap items-center gap-3 rounded-xl border border-zinc-200 p-4 dark:border-zinc-700"
            >
              <div className="min-w-32 flex-1">
                <div className="font-medium">
                  {u.username}
                  {u.id === currentId ? " (you)" : ""}
                </div>
                <div className="text-xs text-zinc-500">
                  {u.role} · {u.active ? "Active" : "Disabled"}
                  {u.must_change_password ? " · Password change required" : ""}
                </div>
              </div>
              <button
                disabled={busy}
                className="ui-button"
                onClick={() =>
                  void action(async () => {
                    await apiRequest(`/api/admin/users/${u.id}`, {
                      method: "PATCH",
                      body: JSON.stringify({
                        role: u.role === "admin" ? "user" : "admin",
                      }),
                    });
                  })
                }
              >
                {u.role === "admin" ? "Make User" : "Make Admin"}
              </button>
              <button
                disabled={busy}
                className="ui-button"
                onClick={() => setReset(u)}
              >
                Reset password
              </button>
              <button
                disabled={busy}
                className="ui-button"
                onClick={() =>
                  void action(async () => {
                    await apiRequest(`/api/admin/users/${u.id}`, {
                      method: "PATCH",
                      body: JSON.stringify({ active: !u.active }),
                    });
                  })
                }
              >
                {u.active ? "Disable" : "Enable"}
              </button>
            </div>
          ))}
        </div>
        {reset && (
          <form
            className="space-y-3 rounded-xl bg-zinc-100 p-4 dark:bg-zinc-800"
            onSubmit={(e) => {
              e.preventDefault();
              const password = new FormData(e.currentTarget).get("password");
              void action(async () => {
                await apiRequest(
                  `/api/admin/users/${reset.id}/reset-password`,
                  { method: "POST", body: JSON.stringify({ password }) },
                );
                setReset(null);
                setNotice(
                  "Password reset. All existing login sessions have been revoked.",
                );
              });
            }}
          >
            <label>
              <span className="ui-label">
                New temporary password for {reset.username}
              </span>
              <input
                autoFocus
                className="ui-field"
                name="password"
                type="password"
                required
                minLength={12}
                maxLength={128}
                autoComplete="new-password"
              />
            </label>
            <div className="flex gap-2">
              <button className="ui-primary" disabled={busy}>
                Reset password
              </button>
              <button
                type="button"
                className="ui-button"
                onClick={() => setReset(null)}
              >
                Cancel
              </button>
            </div>
          </form>
        )}
      </div>
    </ModalShell>
  );
}
