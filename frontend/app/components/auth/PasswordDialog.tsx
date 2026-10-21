"use client";
import { useEffect, useState } from "react";
import { apiRequest, beginAuthChange } from "../../../lib/auth";
import { ModalShell } from "../controls";
export function PasswordDialog({
  embedded = false,
  onBusyChange,
  onLogout,
  required,
  onClose,
  onChanged,
}: {
  onLogout?: () => void;
  embedded?: boolean;
  onBusyChange?: (busy: boolean) => void;
  required: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);
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
      embedded={embedded}
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
        <div className="flex flex-wrap items-center gap-3">
          <button className="ui-primary" disabled={busy}>
            {busy ? "Saving…" : "Save password"}
          </button>
          {required && onLogout && (
            <button
              type="button"
              className="ui-button"
              disabled={busy}
              onClick={onLogout}
            >
              Log out
            </button>
          )}
        </div>
      </form>
    </ModalShell>
  );
}
