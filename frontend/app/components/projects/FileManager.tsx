"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "../../../lib/auth";
import { ModalShell } from "../controls";
type Item = { path: string; size: number };
export function FileManager({
  base,
  label,
  onClose,
  onPrepare,
  exportOnly,
}: {
  base: string | null;
  exportOnly: boolean;
  label: string;
  onClose: () => void;
  onPrepare: () => Promise<void>;
}) {
  const [files, setFiles] = useState<Item[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [query, setQuery] = useState("");
  const [preview, setPreview] = useState<{
    path: string;
    content: string;
    truncated?: boolean;
  } | null>(null);
  const [remove, setRemove] = useState<string | null>(null),
    [pending, setPending] = useState<File | null>(null);
  const [progress, setProgress] = useState("");
  const epoch = useRef(0);
  const refresh = useCallback(async () => {
    if (!base) return;
    const id = ++epoch.current;
    try {
      const r = await apiRequest<{ files: Item[] }>(base + "/files");
      if (id === epoch.current) setFiles(r.files);
    } catch (e) {
      if (id === epoch.current) setError((e as Error).message);
    }
  }, [base]);
  useEffect(() => {
    setFiles([]);
    setPreview(null);
    setError("");
    refresh();
    return () => {
      epoch.current++;
    };
  }, [refresh]);
  async function upload(file: File) {
    if (!base) return;
    if (file.size > 25 * 1024 * 1024) {
      setError("Each file must be 25 MiB or smaller.");
      return;
    }
    setBusy(true);
    setError("");
    setProgress("Uploading " + file.name + "…");
    try {
      await apiRequest(
        base + "/files/upload?path=" + encodeURIComponent(file.name),
        {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: file,
        },
      );
      setPending(null);
      setPreview(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setProgress("");
    }
  }
  async function show(path: string) {
    if (!base) return;
    const id = ++epoch.current;
    setError("");
    setPreview(null);
    try {
      const r = await apiRequest<{
        path: string;
        content: string;
        truncated?: boolean;
      }>(base + "/read?path=" + encodeURIComponent(path));
      if (id === epoch.current) setPreview(r);
    } catch (e) {
      if (id === epoch.current)
        setError(
          (e as Error).message + " You can download binary files instead.",
        );
    }
  }
  async function del() {
    if (!base || !remove) return;
    setBusy(true);
    setError("");
    try {
      await apiRequest(base + "/files?path=" + encodeURIComponent(remove), {
        method: "DELETE",
      });
      setRemove(null);
      setPreview(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <ModalShell
      wide
      title="Files"
      description={
        label +
        (exportOnly
          ? " · Download only"
          : " · Shared across this project’s chats")
      }
      onClose={() => {
        if (!busy) onClose();
      }}
      footer={
        <>
          <span className="mr-auto text-xs text-zinc-500">
            25 MiB per upload/download · Text preview up to 1 MiB
          </span>
          <button className="ui-button" disabled={busy} onClick={onClose}>
            Close
          </button>
        </>
      }
    >
      {!base ? (
        <button
          className="ui-primary"
          onClick={async () => {
            try {
              await onPrepare();
            } catch (e) {
              setError((e as Error).message);
            }
          }}
        >
          Create a Project to work with files
        </button>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap gap-2">
            <input
              className="ui-field min-w-32 flex-1"
              placeholder="Search files…"
              aria-label="Search files"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button className="ui-button" disabled={busy} onClick={refresh}>
              Refresh
            </button>
            {!exportOnly && (
              <label
                className={`ui-primary cursor-pointer ${busy ? "pointer-events-none opacity-50" : ""}`}
              >
                Upload file
                <input
                  className="sr-only"
                  type="file"
                  disabled={busy}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    e.target.value = "";
                    if (!f) return;
                    if (files.some((x) => x.path === f.name)) setPending(f);
                    else upload(f);
                  }}
                />
              </label>
            )}
          </div>
          {pending && (
            <div className="mb-4 rounded-xl border border-amber-300 p-3 text-sm">
              Replace “{pending.name}”? The existing contents will be
              overwritten.
              <div className="mt-2 flex gap-2">
                <button
                  disabled={busy}
                  className="ui-button"
                  onClick={() => upload(pending)}
                >
                  Replace file
                </button>
                <button
                  disabled={busy}
                  className="ui-button"
                  onClick={() => setPending(null)}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
          {remove && (
            <div className="mb-4 rounded-xl border border-rose-300 p-3 text-sm">
              Permanently delete “{remove}”? This affects every chat using these
              files.
              <div className="mt-2 flex gap-2">
                <button
                  disabled={busy}
                  className="ui-button text-rose-600"
                  onClick={del}
                >
                  Delete file
                </button>
                <button
                  disabled={busy}
                  className="ui-button"
                  onClick={() => setRemove(null)}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
          <div className="grid min-h-64 gap-4 md:grid-cols-2">
            <div className="max-h-96 overflow-auto rounded-xl border border-zinc-200 p-2 dark:border-zinc-700">
              {files
                .filter((f) =>
                  f.path.toLowerCase().includes(query.toLowerCase()),
                )
                .map((f) => (
                  <div
                    key={f.path}
                    className="mb-1 rounded-lg p-3 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                  >
                    <button
                      className="block w-full truncate text-left text-sm font-medium text-teal-700 dark:text-teal-300"
                      disabled={exportOnly}
                      onClick={() => show(f.path)}
                      title={f.path}
                    >
                      {f.path}
                    </button>
                    <div className="mt-2 flex items-center gap-3 text-xs">
                      <span className="mr-auto text-zinc-500">
                        {(f.size / 1024).toFixed(1)} KiB
                      </span>
                      <a
                        className="text-teal-700 dark:text-teal-300"
                        href={
                          base +
                          "/files/download?path=" +
                          encodeURIComponent(f.path)
                        }
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Download
                      </a>
                      {!exportOnly && (
                        <button
                          disabled={busy}
                          className="text-rose-600"
                          onClick={() => setRemove(f.path)}
                        >
                          Delete
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              {!files.length && (
                <p className="p-6 text-center text-sm text-zinc-500">
                  {exportOnly
                    ? "No files available for download."
                    : "Upload a file or ask the AI to create one."}
                </p>
              )}
            </div>
            <div className="min-w-0 rounded-xl bg-zinc-50 p-4 dark:bg-zinc-950">
              {preview ? (
                <>
                  <p className="mb-3 truncate text-sm font-medium">
                    {preview.path}
                  </p>
                  {preview.truncated && (
                    <p className="mb-3 text-xs text-amber-600">
                      Preview truncated at 1 MiB. Download for the full file.
                    </p>
                  )}
                  <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">
                    {preview.content || "(empty file)"}
                  </pre>
                </>
              ) : (
                <p className="p-6 text-center text-sm text-zinc-500">
                  {exportOnly
                    ? "Download a file to view its contents."
                    : "Select a file for a safe text preview. Binary files can be downloaded."}
                </p>
              )}
            </div>
          </div>
        </>
      )}
      {progress && (
        <p role="status" className="mt-3 text-sm text-teal-600">
          {progress}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-rose-600">
          {error}
        </p>
      )}
    </ModalShell>
  );
}
