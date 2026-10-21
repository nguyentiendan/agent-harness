"use client";
import { useEffect, useId, useRef, useState } from "react";
import type { User } from "../../../lib/auth";
import { Icon, PATHS } from "../icons";

export const logoutIcon = "M9 5H5v14h4M13 8l4 4-4 4M8 12h13";
export function Avatar({
  username,
  large = false,
}: {
  username: string;
  large?: boolean;
}) {
  return (
    <span
      aria-hidden="true"
      className={`flex shrink-0 items-center justify-center rounded-full bg-teal-700 font-semibold text-white ${large ? "size-12 text-lg" : "size-9 text-sm"}`}
    >
      {Array.from(username.trim())[0]?.toLocaleUpperCase() || "?"}
    </span>
  );
}
export function AccountMenu({
  user,
  compact,
  onSettings,
  onLogout,
}: {
  user: User;
  compact: boolean;
  onSettings: () => void;
  onLogout: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const outside = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  const choose = (action: () => void) => {
    setOpen(false);
    trigger.current?.focus();
    action();
  };
  return (
    <div
      ref={root}
      className={`relative ${compact ? "" : "w-full"}`}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false);
      }}
    >
      {open && (
        <div
          ref={menu}
          id={id}
          role="menu"
          aria-label="Account menu"
          className="absolute bottom-full left-0 z-50 mb-2 w-60 rounded-2xl border border-zinc-200 bg-white p-1.5 shadow-xl dark:border-zinc-700 dark:bg-zinc-900"
          onKeyDown={(e) => {
            const items = Array.from(
              menu.current?.querySelectorAll<HTMLButtonElement>(
                "[role=menuitem]",
              ) || [],
            );
            const at = items.indexOf(
              document.activeElement as HTMLButtonElement,
            );
            if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              setOpen(false);
              trigger.current?.focus();
            }
            if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
              e.preventDefault();
              items[
                e.key === "Home"
                  ? 0
                  : e.key === "End"
                    ? items.length - 1
                    : (at + (e.key === "ArrowDown" ? 1 : -1) + items.length) %
                      items.length
              ]?.focus();
            }
          }}
        >
          <div className="mb-1 border-b border-zinc-100 px-3 py-3 dark:border-zinc-800">
            <p className="truncate text-sm font-semibold">{user.username}</p>
            <p className="mt-1 text-xs capitalize text-zinc-500">
              {user.role} account
            </p>
          </div>
          <button
            role="menuitem"
            onClick={() => choose(onSettings)}
            className="flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-sm hover:bg-zinc-100 focus-visible:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-teal-600 dark:hover:bg-zinc-800 dark:focus-visible:bg-zinc-800"
          >
            <Icon d={PATHS.gear} size={18} />
            Settings
          </button>
          <button
            role="menuitem"
            onClick={() => choose(onLogout)}
            className="flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-sm hover:bg-zinc-100 focus-visible:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-teal-600 dark:hover:bg-zinc-800 dark:focus-visible:bg-zinc-800"
          >
            <Icon d={logoutIcon} size={18} />
            Log out
          </button>
        </div>
      )}
      <button
        ref={trigger}
        aria-label={`Account menu for ${user.username}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (e.key === "ArrowUp" || e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
          }
        }}
        className={`flex min-h-11 items-center rounded-xl transition-colors hover:bg-zinc-200/60 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-600 dark:hover:bg-zinc-800 ${compact ? "justify-center p-1" : "w-full gap-3 px-2 py-2 text-left"}`}
      >
        <Avatar username={user.username} />
        {!compact && (
          <>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">
                {user.username}
              </span>
              <span className="block text-xs capitalize text-zinc-500">
                {user.role}
              </span>
            </span>
            <Icon d="m9 14 3-3 3 3" />
          </>
        )}
      </button>
    </div>
  );
}
