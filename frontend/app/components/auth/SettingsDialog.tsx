"use client";
import { useEffect, useId, useRef, useState } from "react";
import type { User } from "../../../lib/auth";
import { Icon, PATHS } from "../icons";
import { UsersPanel } from "../admin/UsersPanel";
import { UsagePanel } from "../admin/UsagePanel";
import { PasswordDialog } from "./PasswordDialog";
import { Avatar, logoutIcon } from "./AccountMenu";
import ThemeToggle from "../../theme-toggle";

type Section = "general" | "usage" | "users" | "limits" | "password";
const sections: {
  id: Section;
  label: string;
  icon: string;
  admin?: boolean;
  description: string;
}[] = [
  {
    id: "general",
    label: "General",
    icon: PATHS.gear,
    description: "Your account and preferences.",
  },
  {
    id: "usage",
    label: "My Usage",
    icon: "M5 20v-6M12 20V4M19 20V9",
    description: "Your monthly AI budget and activity.",
  },
  {
    id: "users",
    label: "Manage Users",
    icon: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M20 8v6m-3-3h6",
    admin: true,
    description: "Create accounts and manage access.",
  },
  {
    id: "limits",
    label: "AI Limits",
    icon: "M4 7h16M4 17h16M8 4v6M16 14v6",
    admin: true,
    description: "Manage model access, budgets and usage by account.",
  },
  {
    id: "password",
    label: "Change Password",
    icon: "M5 10h14v11H5zM8 10V6a4 4 0 0 1 8 0v4M12 14v3",
    description: "Changing your password signs out all your devices.",
  },
];
export function SettingsDialog({
  user,
  onClose,
  onLogout,
  onPasswordChanged,
}: {
  user: User;
  onClose: () => void;
  onLogout: () => void;
  onPasswordChanged: () => void;
}) {
  const [section, setSection] = useState<Section>("general");
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();
  const current = sections.find((s) => s.id === section)!;
  useEffect(() => {
    const node = dialog.current!;
    const previous = document.activeElement as HTMLElement | null;
    node.showModal();
    return () => {
      node.close();
      previous?.focus();
    };
  }, []);
  const close = () => {
    if (!busy) onClose();
  };
  return (
    <dialog
      ref={dialog}
      aria-labelledby={title}
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
      className="fixed inset-0 m-auto h-[min(720px,90dvh)] max-h-[90dvh] w-[calc(100%-2rem)] max-w-5xl overflow-hidden rounded-2xl border border-zinc-200 bg-white p-0 text-zinc-900 shadow-2xl backdrop:bg-zinc-950/40 backdrop:backdrop-blur-sm dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
    >
      <div className="relative flex h-full flex-col sm:flex-row">
        <nav
          aria-label="Settings sections"
          className="shrink-0 border-b border-zinc-200 bg-zinc-50 p-3 sm:w-52 sm:border-b-0 sm:border-r sm:p-4 dark:border-zinc-800 dark:bg-zinc-950/50"
        >
          <h2 className="mb-3 px-3 pt-1 text-sm font-semibold sm:mb-6">
            Settings
          </h2>
          <div className="flex gap-1 overflow-x-auto pb-1 sm:flex-col">
            {sections
              .filter((s) => !s.admin || user.role === "admin")
              .map((s) => (
                <button
                  key={s.id}
                  disabled={busy}
                  aria-current={section === s.id ? "page" : undefined}
                  onClick={() => setSection(s.id)}
                  className={`flex min-h-11 shrink-0 items-center gap-3 whitespace-nowrap rounded-lg px-3 text-left text-sm transition-colors focus-visible:outline-2 focus-visible:outline-teal-600 disabled:opacity-60 ${section === s.id ? "bg-zinc-200/70 font-medium text-zinc-950 dark:bg-zinc-800 dark:text-white" : "text-zinc-600 hover:bg-zinc-200/50 dark:text-zinc-400 dark:hover:bg-zinc-800/70"}`}
                >
                  <Icon d={s.icon} size={17} />
                  {s.label}
                </button>
              ))}
          </div>
        </nav>
        <section className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain p-5 sm:p-9">
          <header className="mb-8 pr-10">
            <h2 id={title} className="text-xl font-semibold tracking-tight">
              {current.label}
            </h2>
            <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
              {current.description}
            </p>
          </header>
          {section === "general" && (
            <div className="space-y-8">
              <section>
                <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">
                  Account
                </h3>
                <div className="rounded-2xl border border-zinc-200 p-5 dark:border-zinc-700">
                  <div className="flex flex-wrap items-center gap-4">
                    <Avatar username={user.username} large />
                    <div className="min-w-0 flex-1">
                      <p className="break-words font-semibold">
                        {user.username}
                      </p>
                      <p className="mt-1 text-sm capitalize text-zinc-500">
                        {user.role} account
                      </p>
                    </div>
                    <button className="ui-button" onClick={onLogout}>
                      <Icon d={logoutIcon} />
                      Log out
                    </button>
                  </div>
                  <dl className="mt-5 space-y-3 border-t border-zinc-100 pt-4 text-sm dark:border-zinc-800">
                    <div className="flex justify-between gap-6">
                      <dt className="text-zinc-500">Username</dt>
                      <dd className="min-w-0 break-all text-right">
                        {user.username}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-6">
                      <dt className="text-zinc-500">Email</dt>
                      <dd className="text-zinc-500">Not provided</dd>
                    </div>
                  </dl>
                </div>
              </section>
              <section>
                <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">
                  Appearance
                </h3>
                <div className="flex items-center justify-between gap-4 rounded-2xl border border-zinc-200 p-5 dark:border-zinc-700">
                  <div>
                    <p className="text-sm font-medium">Theme</p>
                    <p className="mt-1 text-xs text-zinc-500">
                      Switch between light and dark.
                    </p>
                  </div>
                  <ThemeToggle />
                </div>
              </section>
            </div>
          )}
          {section === "usage" && (
            <UsagePanel
              embedded
              user={user}
              onClose={close}
              onBusyChange={setBusy}
            />
          )}
          {section === "users" && user.role === "admin" && (
            <UsersPanel
              embedded
              currentId={user.id}
              onClose={close}
              onBusyChange={setBusy}
            />
          )}
          {section === "limits" && user.role === "admin" && (
            <UsagePanel
              key="limits"
              embedded
              admin
              user={user}
              onClose={close}
              onBusyChange={setBusy}
            />
          )}
          {section === "password" && (
            <PasswordDialog
              embedded
              required={false}
              onClose={close}
              onChanged={onPasswordChanged}
              onBusyChange={setBusy}
            />
          )}
        </section>
        <button
          autoFocus
          disabled={busy}
          aria-label="Close settings"
          onClick={close}
          className="ui-icon-button absolute right-3 top-3 disabled:opacity-50"
        >
          <Icon d={PATHS.x} size={18} />
        </button>
      </div>
    </dialog>
  );
}
