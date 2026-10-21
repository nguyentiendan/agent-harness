"use client";
import { Icon, PATHS } from "../icons";
import type { Project } from "./ProjectDialog";
export function ProjectList({
  projects,
  selected,
  onSelect,
  onNew,
  onEdit,
  allUsers,
}: {
  projects: Project[];
  selected: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  onEdit: (p: Project) => void;
  allUsers: boolean;
}) {
  return (
    <div className="mt-4 border-t border-zinc-200 pt-3 dark:border-zinc-800">
      <div className="mb-2 flex items-center justify-between text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
        <span>Projects</span>
        <button
          title="New project"
          aria-label="New project"
          className="ui-icon-button"
          onClick={onNew}
        >
          <Icon d={PATHS.plus} size={15} />
        </button>
      </div>
      <div className="max-h-48 overflow-y-auto">
        {projects.map((p) => (
          <div
            key={p.id}
            className={`flex items-center rounded-lg ${selected === p.id ? "bg-teal-50 text-teal-700 dark:bg-teal-500/10" : "hover:bg-zinc-100 dark:hover:bg-zinc-800"}`}
          >
            <button
              className="flex min-w-0 flex-1 items-center gap-2 px-3 py-2 text-left text-sm"
              onClick={() => onSelect(p.id)}
            >
              <Icon d={PATHS.folder} size={16} />
              <span className="truncate">
                {p.name}
                {allUsers && (
                  <small className="block text-zinc-500">
                    {p.owner_username}
                  </small>
                )}
              </span>
            </button>
            <button
              className="ui-icon-button shrink-0"
              aria-label={`Settings for ${p.name}`}
              onClick={() => onEdit(p)}
            >
              <Icon d={PATHS.gear} size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
