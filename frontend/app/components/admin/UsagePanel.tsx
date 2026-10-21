"use client";
import { useEffect, useState } from "react";
import { apiRequest, type User } from "../../../lib/auth";
import { ModalShell, SelectField } from "../controls";

type Limits = {
  monthly_budget: number | null;
  models: string[];
  concurrent_runs: number;
  max_output_tokens: number;
  sandbox_profile: string;
};
type Call = {
  id: string;
  model: string;
  cost: number | null;
  reserved: number;
  status: string;
  created_at: number;
};
type Usage = Limits & {
  period: string;
  confirmed_cost: number;
  pending_reserved: number;
  pending_calls: number;
  available: number | null;
  overspend: number;
  calls: Call[];
};
const dollars = (n: number | null) =>
  n === null ? "Not assigned" : `$${n.toFixed(6)}`;

export function UsagePanel({
  user,
  admin = false,
  onClose,
  embedded = false,
  onBusyChange,
}: {
  user: User;
  admin?: boolean;
  embedded?: boolean;
  onBusyChange?: (busy: boolean) => void;
  onClose: () => void;
}) {
  const [users, setUsers] = useState<User[]>([]);
  const [uid, setUid] = useState(user.id);
  const [models, setModels] = useState<{ name: string; ceiling: string }[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [limits, setLimits] = useState<Limits | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);
  const [pending, setPending] = useState<{ calls: Call[]; has_more: boolean }>({
    calls: [],
    has_more: false,
  });
  const [offset, setOffset] = useState(0);
  const [resolve, setResolve] = useState<Call | null>(null);
  const refresh = async () => {
    const r = await apiRequest<Usage>(
      admin ? `/api/admin/users/${uid}/usage` : "/api/usage",
    );
    setUsage(r);
    setLimits(r);
    if (admin)
      setPending(
        await apiRequest(`/api/admin/users/${uid}/pending?offset=${offset}`),
      );
  };
  useEffect(() => {
    if (!admin) return;
    Promise.all([
      apiRequest<{ users: User[] }>("/api/admin/users"),
      apiRequest<{ models: { name: string; ceiling: string }[] }>(
        "/api/admin/models",
      ),
    ])
      .then(([u, m]) => {
        setUsers(u.users);
        setModels(m.models);
      })
      .catch((e) => setError(e.message));
  }, [admin]);
  useEffect(() => {
    let live = true;
    setUsage(null);
    setLimits(null);
    setResolve(null);
    setError("");
    setNotice("");
    apiRequest<Usage>(admin ? `/api/admin/users/${uid}/usage` : "/api/usage")
      .then((r) => {
        if (live) {
          setUsage(r);
          setLimits(r);
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [uid, admin]);
  useEffect(() => {
    if (!admin) return;
    let live = true;
    setPending({ calls: [], has_more: false });
    apiRequest<{ calls: Call[]; has_more: boolean }>(
      `/api/admin/users/${uid}/pending?offset=${offset}`,
    )
      .then((r) => {
        if (live) setPending(r);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [uid, offset, admin]);
  const action = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
      await refresh();
      setNotice(message);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <ModalShell
      embedded={embedded}
      wide
      title={admin ? "AI limits & usage" : "My AI usage"}
      description="Monthly budget in USD · resets on the first day of each month (UTC)."
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <div className="space-y-5">
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
        {admin && (
          <div className={busy ? "pointer-events-none opacity-60" : ""}>
            <SelectField
              label="User"
              value={uid}
              onChange={(value) => {
                setUid(value);
                setOffset(0);
              }}
              options={users.map((u) => ({
                value: u.id,
                label: u.username + (u.active ? "" : " (disabled)"),
              }))}
            />
          </div>
        )}
        {!usage && <p className="text-sm text-zinc-500">Loading usage…</p>}
        {usage && (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                ["Budget", usage.monthly_budget],
                ["Confirmed", usage.confirmed_cost],
                ["Pending reserve", usage.pending_reserved],
                ["Available", usage.available],
              ].map(([label, n]) => (
                <div
                  key={String(label)}
                  className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-700"
                >
                  <div className="text-xs text-zinc-500">{label}</div>
                  <div className="mt-2 break-all font-semibold">
                    {dollars(n as number | null)}
                  </div>
                </div>
              ))}
            </div>
            <p className="text-xs text-zinc-500">
              {usage.period} · {usage.pending_calls} pending calls. Pending
              reserves are held until cost is confirmed. Each call reserves the
              model’s full context cost before sending; unused reserve is
              released when cost arrives.
            </p>
            {usage.overspend > 0 && (
              <p role="alert" className="text-sm text-rose-600">
                Recorded cost exceeds budget by {dollars(usage.overspend)}.
                Further calls are blocked.
              </p>
            )}
            {usage.monthly_budget === null && (
              <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900">
                An administrator must assign a budget and models before this
                account can use AI.
              </p>
            )}
            {admin && limits && (
              <form
                className="space-y-4 rounded-xl border border-zinc-200 p-4 dark:border-zinc-700"
                onSubmit={(e) => {
                  e.preventDefault();
                  void action(
                    () =>
                      apiRequest(`/api/admin/users/${uid}/limits`, {
                        method: "PUT",
                        body: JSON.stringify({
                          monthly_budget: limits.monthly_budget,
                          models: limits.models,
                          concurrent_runs: limits.concurrent_runs,
                          max_output_tokens: limits.max_output_tokens,
                        }),
                      }),
                    "Limits saved. Refresh the chat page to update available models.",
                  );
                }}
              >
                <div className="grid gap-4 sm:grid-cols-3">
                  <label>
                    <span className="ui-label">Monthly budget (USD)</span>
                    <input
                      className="ui-field"
                      type="number"
                      min="0"
                      max="1000000"
                      step="0.01"
                      value={limits.monthly_budget ?? ""}
                      onChange={(e) =>
                        setLimits({
                          ...limits,
                          monthly_budget:
                            e.target.value === ""
                              ? null
                              : Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    <span className="ui-label">Concurrent runs (1–4)</span>
                    <input
                      required
                      className="ui-field"
                      type="number"
                      min="1"
                      max="4"
                      value={limits.concurrent_runs}
                      onChange={(e) =>
                        setLimits({
                          ...limits,
                          concurrent_runs: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    <span className="ui-label">Output tokens (1–4096)</span>
                    <input
                      required
                      className="ui-field"
                      type="number"
                      min="1"
                      max="4096"
                      value={limits.max_output_tokens}
                      onChange={(e) =>
                        setLimits({
                          ...limits,
                          max_output_tokens: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                </div>
                <fieldset className="space-y-2">
                  <legend className="ui-label">
                    Allowed models with verified pricing
                  </legend>
                  {models.map((m) => (
                    <label
                      key={m.name}
                      className="flex items-center gap-2 text-sm"
                    >
                      <input
                        type="checkbox"
                        checked={limits.models.includes(m.name)}
                        onChange={(e) =>
                          setLimits({
                            ...limits,
                            models: e.target.checked
                              ? [...limits.models, m.name]
                              : limits.models.filter((x) => x !== m.name),
                          })
                        }
                      />
                      <span>
                        {m.name}{" "}
                        <span className="text-xs text-zinc-500">
                          · reserve up to {dollars(Number(m.ceiling))}/call
                        </span>
                      </span>
                    </label>
                  ))}
                </fieldset>
                <p className="text-xs text-zinc-500">
                  Sandbox: {limits.sandbox_profile}. Stop active runs before
                  changing limits. Zero budget or no models blocks AI access.
                </p>
                <div className="flex flex-wrap gap-2">
                  <button disabled={busy} className="ui-primary">
                    Save limits
                  </button>
                  <button
                    disabled={busy}
                    className="ui-button"
                    type="button"
                    onClick={() =>
                      void action(
                        () =>
                          apiRequest(`/api/admin/users/${uid}/key/rotate`, {
                            method: "POST",
                          }),
                        "Key revocation queued. A replacement is created on the next permitted AI call.",
                      )
                    }
                  >
                    Rotate AI key
                  </button>
                </div>
              </form>
            )}
            {!admin && (
              <p className="text-sm text-zinc-500">
                Allowed models: {usage.models.join(", ") || "None"} ·{" "}
                {usage.concurrent_runs} concurrent runs
              </p>
            )}
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">Recent calls (up to 100)</h3>
              <button
                className="ui-button"
                disabled={busy}
                onClick={() => void action(async () => {}, "Usage refreshed.")}
              >
                Refresh
              </button>
            </div>
            <div className="max-h-64 overflow-auto space-y-2">
              {usage.calls.length === 0 && (
                <p className="text-sm text-zinc-500">No AI calls this month.</p>
              )}
              {usage.calls.map((c) => (
                <div
                  key={c.id}
                  className="flex flex-wrap items-center gap-3 rounded-xl border border-zinc-200 p-3 text-sm dark:border-zinc-700"
                >
                  <div className="flex-1">
                    <div>{c.model}</div>
                    <div className="text-xs text-zinc-500">
                      {new Date(c.created_at * 1000).toLocaleString()} ·{" "}
                      {c.status}
                    </div>
                  </div>
                  <span>
                    {c.cost === null
                      ? `Pending (${dollars(c.reserved)} reserved)`
                      : dollars(c.cost)}
                  </span>
                  {admin && c.cost === null && (
                    <button
                      className="ui-button"
                      disabled={busy}
                      onClick={() => setResolve(c)}
                    >
                      Reconcile
                    </button>
                  )}
                </div>
              ))}
            </div>
            {admin && (
              <section className="space-y-3">
                <h3 className="font-semibold">Unresolved calls · all months</h3>
                {pending.calls.length === 0 && (
                  <p className="text-sm text-zinc-500">
                    No unresolved calls on this page.
                  </p>
                )}
                {pending.calls.map((c) => (
                  <div
                    key={c.id}
                    className="flex flex-wrap items-center gap-3 rounded-xl border border-zinc-200 p-3 text-sm dark:border-zinc-700"
                  >
                    <div className="flex-1">
                      {c.model}
                      <div className="text-xs text-zinc-500">
                        {new Date(c.created_at * 1000).toLocaleString()}
                      </div>
                    </div>
                    <span>{dollars(c.reserved)} reserved</span>
                    <button
                      className="ui-button"
                      disabled={busy}
                      onClick={() => setResolve(c)}
                    >
                      Reconcile
                    </button>
                  </div>
                ))}
                <div className="flex gap-2">
                  <button
                    className="ui-button"
                    disabled={busy || offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - 100))}
                  >
                    Previous
                  </button>
                  <button
                    className="ui-button"
                    disabled={busy || !pending.has_more}
                    onClick={() => setOffset(offset + 100)}
                  >
                    Next
                  </button>
                </div>
              </section>
            )}
            {resolve && (
              <form
                className="space-y-3 rounded-xl bg-zinc-100 p-4 dark:bg-zinc-800"
                onSubmit={(e) => {
                  e.preventDefault();
                  const d = new FormData(e.currentTarget);
                  void action(async () => {
                    await apiRequest(`/api/admin/usage/${resolve.id}/resolve`, {
                      method: "POST",
                      body: JSON.stringify({
                        cost: Number(d.get("cost")),
                        reason: d.get("reason"),
                      }),
                    });
                    setResolve(null);
                  }, "Cost reconciled and audit recorded.");
                }}
              >
                <p className="text-sm">
                  Record confirmed gateway/provider cost for this pending call.
                  This action releases its reserve and cannot be repeated. Stop
                  the run first.
                </p>
                <label className="block">
                  <span className="ui-label">Confirmed cost (USD)</span>
                  <input
                    className="ui-field"
                    name="cost"
                    type="number"
                    min="0"
                    max="1000000"
                    step="0.0000000001"
                    required
                  />
                </label>
                <label className="block">
                  <span className="ui-label">
                    Evidence / reason (at least 10 characters)
                  </span>
                  <textarea
                    className="ui-field"
                    name="reason"
                    minLength={10}
                    maxLength={500}
                    required
                  />
                </label>
                <div className="flex gap-2">
                  <button className="ui-primary" disabled={busy}>
                    Confirm reconciliation
                  </button>
                  <button
                    type="button"
                    className="ui-button"
                    disabled={busy}
                    onClick={() => setResolve(null)}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            )}
          </>
        )}
      </div>
    </ModalShell>
  );
}
