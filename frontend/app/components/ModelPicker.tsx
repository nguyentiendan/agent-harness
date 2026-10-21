"use client";
import { useEffect, useState } from "react";
import { apiRequest } from "../../lib/auth";
import { SelectField } from "./controls";

export function ModelPicker({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (model: string) => void;
  disabled: boolean;
}) {
  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    apiRequest<{ models: string[] }>("/api/config")
      .then((r) => {
        if (live) setModels(r.models);
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [retry]);
  return (
    <div className="min-w-0 max-w-full sm:max-w-80">
      <SelectField
        id="chat-model-select"
        label="Model for next message"
        value={value}
        disabled={disabled || loading || !!error || !models.length}
        className="inline-flex min-h-9 max-w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-zinc-600 transition-colors hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-teal-600 disabled:cursor-not-allowed disabled:opacity-60 dark:text-zinc-300 dark:hover:bg-zinc-800"
        options={
          loading || error || !models.length
            ? [
                {
                  value: "",
                  label: loading
                    ? "Loading models…"
                    : error
                      ? "Models unavailable"
                      : "No models assigned",
                },
              ]
            : [
                { value: "", label: "Server default" },
                ...models.map((model) => ({ value: model, label: model })),
              ]
        }
        onChange={onChange}
      />
      {error && (
        <p role="alert" className="px-2 text-xs text-rose-600">
          {error}{" "}
          <button
            type="button"
            className="underline"
            onClick={() => setRetry((n) => n + 1)}
          >
            Retry
          </button>
        </p>
      )}
      {!loading && !error && !models.length && (
        <p className="px-2 text-xs text-zinc-500">
          Ask an admin to assign models in AI Limits.
        </p>
      )}
    </div>
  );
}
