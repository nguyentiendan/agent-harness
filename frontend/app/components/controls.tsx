"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Icon, PATHS } from "./icons";

export function ModalShell({ title, description, icon = PATHS.gear, onClose, children, footer, wide = false }: {
  title: string; description?: string; icon?: string; onClose: () => void;
  children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  useEffect(() => {
    const dialog = ref.current!;
    const previous = document.activeElement as HTMLElement | null;
    dialog.showModal();
    return () => { dialog.close(); previous?.focus(); };
  }, []);
  return (
    <dialog ref={ref} aria-labelledby={titleId} aria-describedby={description ? descriptionId : undefined}
      onCancel={(e) => { e.preventDefault(); onClose(); }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      className={`fixed inset-0 m-auto max-h-[90dvh] w-[calc(100%-2rem)] overflow-visible rounded-2xl border border-zinc-200 bg-white p-0 text-zinc-900 shadow-2xl backdrop:bg-zinc-950/50 backdrop:backdrop-blur-sm dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 ${wide ? "max-w-4xl" : "max-w-xl"}`}>
      <div className="flex max-h-[90dvh] flex-col overflow-hidden rounded-2xl" onClick={(e) => e.stopPropagation()}>
        <header className="flex shrink-0 items-start gap-4 border-b border-zinc-200 bg-zinc-50/80 p-5 sm:p-6 dark:border-zinc-800 dark:bg-zinc-950/30">
          <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-teal-600/10 text-teal-700 ring-1 ring-teal-600/10 dark:text-teal-300"><Icon d={icon} size={21} /></span>
          <div className="min-w-0 flex-1"><h2 id={titleId} className="text-lg font-semibold tracking-tight">{title}</h2>{description && <p id={descriptionId} className="mt-1 text-sm leading-relaxed text-zinc-500 dark:text-zinc-400">{description}</p>}</div>
          <button type="button" aria-label="Close dialog" onClick={onClose} className="ui-icon-button -mr-2 -mt-2"><Icon d={PATHS.x} size={18} /></button>
        </header>
        <div className="min-h-0 overflow-y-auto p-5 sm:p-6">{children}</div>
        {footer && <footer className="flex shrink-0 items-center justify-end gap-3 border-t border-zinc-200 bg-zinc-50 px-6 py-4 dark:border-zinc-800 dark:bg-zinc-950/30">{footer}</footer>}
      </div>
    </dialog>
  );
}

type Option = { value: string; label: string };
export function SelectField({ id, label, value, options, onChange }: {
  id?: string; label: string; value: string; options: Option[]; onChange: (value: string) => void;
}) {
  const uid = useId();
  const listId = `${uid}-list`;
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [position, setPosition] = useState({ left: 0, top: 0, width: 0, maxHeight: 280 });
  const [host, setHost] = useState<Element | null>(null);
  const selected = options.find((option) => option.value === value);
  const show = () => {
    if (!options.length) return;
    setActive(Math.max(0, options.findIndex((option) => option.value === value)));
    setHost(trigger.current?.closest("dialog") || document.body);
    setOpen(true);
  };
  useEffect(() => {
    if (!open) return;
    const positionList = () => {
      const rect = trigger.current!.getBoundingClientRect();
      const width = Math.min(Math.max(240, rect.width), window.innerWidth - 24);
      const below = window.innerHeight - rect.bottom - 16;
      const above = below < 200 && rect.top > below;
      const maxHeight = Math.min(280, Math.max(80, above ? rect.top - 16 : below));
      setPosition({ left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)), top: above ? rect.top - Math.min(maxHeight, options.length * 44 + 12) - 6 : rect.bottom + 6, width, maxHeight });
    };
    const outside = (event: PointerEvent) => {
      if (!trigger.current?.contains(event.target as Node) && !list.current?.contains(event.target as Node)) setOpen(false);
    };
    positionList();
    window.addEventListener("resize", positionList);
    window.addEventListener("scroll", positionList, true);
    document.addEventListener("pointerdown", outside);
    return () => { window.removeEventListener("resize", positionList); window.removeEventListener("scroll", positionList, true); document.removeEventListener("pointerdown", outside); };
  }, [open, options.length]);
  useEffect(() => { if (open) list.current?.children[active]?.scrollIntoView({ block: "nearest" }); }, [active, open]);
  const choose = (index: number) => { if (options[index]) onChange(options[index].value); setOpen(false); trigger.current?.focus(); };
  return <>
    <button ref={trigger} id={id} type="button" role="combobox" aria-label={label} aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listId : undefined}
      aria-activedescendant={open ? `${uid}-${active}` : undefined}
      className="ui-field flex items-center justify-between gap-3 text-left" onClick={() => open ? setOpen(false) : show()}
      onKeyDown={(e) => {
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
          e.preventDefault(); if (!open) { show(); return; }
          setActive((i) => e.key === "Home" ? 0 : e.key === "End" ? options.length - 1 : (i + (e.key === "ArrowDown" ? 1 : -1) + options.length) % options.length);
        } else if (e.key === "Escape" && open) { e.preventDefault(); e.stopPropagation(); setOpen(false); }
        else if ((e.key === "Enter" || e.key === " ") && open) { e.preventDefault(); choose(active); }
        else if (e.key === "Tab") setOpen(false);
        else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) { const index = options.findIndex((o) => o.label.toLowerCase().startsWith(e.key.toLowerCase())); if (index >= 0) { e.preventDefault(); if (!open) show(); setActive(index); } }
      }}>
      <span className="truncate">{selected?.label || value || "Select an option"}</span>
      <svg className={`shrink-0 text-zinc-400 transition-transform ${open ? "rotate-180" : ""}`} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
    </button>
    {open && host && createPortal(<div ref={list} id={listId} role="listbox" aria-label={label} style={position}
      className="fixed z-[100] overflow-y-auto rounded-xl border border-zinc-200 bg-white p-1.5 shadow-xl ring-1 ring-black/5 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100">
      {options.map((option, i) => <button key={option.value} id={`${uid}-${i}`} role="option" aria-selected={option.value === value} tabIndex={-1} type="button"
        onMouseDown={(e) => e.preventDefault()} onPointerMove={() => setActive(i)} onClick={() => choose(i)}
        className={`flex min-h-11 w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm ${active === i ? "bg-teal-50 text-teal-800 dark:bg-teal-500/15 dark:text-teal-200" : "text-zinc-700 dark:text-zinc-300"}`}>
        <span className="break-all">{option.label}</span>{option.value === value && <Icon d={PATHS.check} size={16} />}
      </button>)}
    </div>, host)}
  </>;
}
