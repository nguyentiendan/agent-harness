"use client";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiRequest } from "../../lib/auth";
import { Icon, PATHS } from "./icons";

type Model = {
  id: string;
  name: string;
  provider: string;
  price_class: string;
  selectable: boolean;
  reason: string | null;
};
export function ModelPicker({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (model: string) => void;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<Model[]>([]);
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("all");
  const [offset, setOffset] = useState(0);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [position, setPosition] = useState({
    left: 12,
    bottom: 70,
    width: 360,
    maxHeight: 440,
  });
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);
  useEffect(() => {
    if (!open) return;
    const locate = () => {
      const r = trigger.current!.getBoundingClientRect();
      const width = Math.min(400, window.innerWidth - 24);
      setPosition({
        left: Math.max(12, Math.min(r.left, window.innerWidth - width - 12)),
        bottom: window.innerHeight - r.top + 8,
        width,
        maxHeight: Math.max(160, Math.min(460, r.top - 20)),
      });
    };
    const outside = (e: PointerEvent) => {
      if (
        !popup.current?.contains(e.target as Node) &&
        !trigger.current?.contains(e.target as Node)
      )
        setOpen(false);
    };
    locate();
    input.current?.focus();
    document.addEventListener("pointerdown", outside);
    window.addEventListener("resize", locate);
    return () => {
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("resize", locate);
    };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    let live = true;
    setLoading(true);
    setError("");
    const timer = setTimeout(() => {
      apiRequest<{ models: Model[]; has_more: boolean }>(
        `/api/models?search=${encodeURIComponent(search)}&kind=${kind}&offset=${offset}&limit=50`,
      )
        .then((r) => {
          if (live) {
            setModels(r.models);
            setMore(r.has_more);
          }
        })
        .catch((e) => {
          if (live) {
            setModels([]);
            setError(e.message);
          }
        })
        .finally(() => {
          if (live) setLoading(false);
        });
    }, 200);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [open, search, kind, offset, retry]);
  const choose = (id: string) => {
    onChange(id);
    setOpen(false);
    trigger.current?.focus();
  };
  return (
    <div className="min-w-0 max-w-full sm:max-w-96">
      <button
        ref={trigger}
        id="chat-model-select"
        type="button"
        aria-label="Model for next message"
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen(!open)}
        className="inline-flex min-h-9 max-w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-zinc-600 hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-teal-600 disabled:opacity-60 dark:text-zinc-300 dark:hover:bg-zinc-800"
      >
        <span className="truncate">{value || "Automatic · Free first"}</span>
        <Icon d="m6 9 6 6 6-6" size={14} />
      </button>
      {open &&
        createPortal(
          <div
            ref={popup}
            role="dialog"
            aria-label="Choose model"
            style={position}
            className="fixed z-[100] flex flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white text-zinc-900 shadow-xl dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                setOpen(false);
                trigger.current?.focus();
              }
            }}
          >
            <div className="space-y-3 border-b border-zinc-200 p-3 dark:border-zinc-800">
              <div className="flex items-center gap-2">
                <input
                  ref={input}
                  className="ui-field"
                  aria-label="Search models"
                  placeholder="Search model or provider…"
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value);
                    setOffset(0);
                  }}
                />
                <button
                  className="ui-icon-button"
                  aria-label="Close model picker"
                  onClick={() => {
                    setOpen(false);
                    trigger.current?.focus();
                  }}
                >
                  <Icon d={PATHS.x} />
                </button>
              </div>
              <div className="flex gap-2">
                {["all", "free", "paid"].map((k) => (
                  <button
                    key={k}
                    aria-pressed={kind === k}
                    className={`rounded-full px-3 py-1.5 text-xs capitalize ${kind === k ? "bg-teal-700 text-white" : "bg-zinc-100 dark:bg-zinc-800"}`}
                    onClick={() => {
                      setKind(k);
                      setOffset(0);
                    }}
                  >
                    {k}
                  </button>
                ))}
              </div>
            </div>
            <div className="min-h-0 overflow-y-auto p-1.5" aria-busy={loading}>
              <button
                className="w-full rounded-lg px-3 py-3 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
                onClick={() => choose("")}
              >
                Automatic{" "}
                <span className="text-xs text-zinc-500">
                  · prefer an available Free model
                </span>
              </button>
              {loading ? (
                <p role="status" className="p-4 text-sm text-zinc-500">
                  Loading models…
                </p>
              ) : error ? (
                <p role="alert" className="p-3 text-sm text-rose-600">
                  {error}{" "}
                  <button
                    className="underline"
                    onClick={() => setRetry((r) => r + 1)}
                  >
                    Retry
                  </button>
                </p>
              ) : models.length === 0 ? (
                <p className="p-3 text-sm text-zinc-500">
                  No compatible models found.
                </p>
              ) : (
                models.map((m) => (
                  <button
                    key={m.id}
                    disabled={!m.selectable}
                    title={m.reason || m.id}
                    onClick={() => choose(m.id)}
                    className={`flex w-full items-start gap-3 rounded-lg p-3 text-left hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-teal-600 disabled:cursor-not-allowed disabled:opacity-60 dark:hover:bg-zinc-800 ${value === m.id ? "bg-teal-50 dark:bg-teal-950" : ""}`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="break-words text-sm font-medium">
                        {m.name}
                      </div>
                      <div className="mt-1 break-all text-xs text-zinc-500">
                        {m.id}
                      </div>
                      {m.reason && (
                        <p className="mt-1 text-xs text-zinc-500">{m.reason}</p>
                      )}
                    </div>
                    <span
                      className={`rounded-full px-2 py-1 text-[11px] capitalize ${m.price_class === "free" ? "bg-teal-50 text-teal-800 dark:bg-teal-950 dark:text-teal-300" : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"}`}
                    >
                      {m.price_class}
                    </span>
                  </button>
                ))
              )}
            </div>
            <div className="flex justify-between border-t border-zinc-200 p-2 dark:border-zinc-800">
              <button
                className="ui-button"
                disabled={loading || offset === 0}
                onClick={() => setOffset(Math.max(0, offset - 50))}
              >
                Previous
              </button>
              <button
                className="ui-button"
                disabled={loading || !more}
                onClick={() => setOffset(offset + 50)}
              >
                Next
              </button>
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
