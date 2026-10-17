import React from "react";

/** Minimal, XSS-safe Markdown renderer (no raw HTML is ever injected). */

function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`\n]+`|\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let i = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${i++}`;
    if (token.startsWith("**")) {
      nodes.push(
        <strong key={key} className="font-semibold text-zinc-900 dark:text-zinc-50">
          {token.slice(2, -2)}
        </strong>,
      );
    } else if (token.startsWith("`")) {
      nodes.push(
        <code
          key={key}
          className="rounded bg-zinc-200/80 px-1 py-0.5 font-mono text-[0.85em] text-teal-800 dark:bg-zinc-800 dark:text-teal-300"
        >
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith("[")) {
      const close = token.indexOf("]");
      const label = token.slice(1, close);
      const href = token.slice(close + 2, -1);
      nodes.push(
        <a
          key={key}
          href={href}
          target="_blank"
          rel="noreferrer"
          className="font-medium text-teal-700 underline decoration-teal-600/40 underline-offset-2 hover:decoration-teal-600 dark:text-teal-300 dark:decoration-teal-400/40 dark:hover:decoration-teal-300"
        >
          {label}
        </a>,
      );
    } else {
      nodes.push(<em key={key}>{token.slice(1, -1)}</em>);
    }
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export default function Markdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  const lines = text.split("\n");
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trimStart().startsWith("```")) {
      const lang = line.trim().slice(3).trim();
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) {
        code.push(lines[i]);
        i++;
      }
      i++;
      blocks.push(
        <div key={key++} className="my-2 overflow-hidden rounded-lg bg-zinc-950 ring-1 ring-zinc-800">
          {lang && (
            <div className="border-b border-zinc-800 px-3 py-1.5 font-mono text-[11px] text-zinc-500">
              {lang}
            </div>
          )}
          <pre className="overflow-x-auto p-3 font-mono text-[12.5px] leading-relaxed text-zinc-100">
            {code.join("\n")}
          </pre>
        </div>,
      );
      continue;
    }

    if (!line.trim()) {
      i++;
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const cls =
        level === 1
          ? "mt-3 mb-1.5 text-[17px] font-bold text-zinc-900 dark:text-zinc-50"
          : level === 2
            ? "mt-3 mb-1.5 text-[15px] font-bold text-zinc-900 dark:text-zinc-50"
            : "mt-2.5 mb-1 text-[14px] font-semibold text-zinc-900 dark:text-zinc-50";
      blocks.push(
        <div key={key++} className={cls}>
          {renderInline(heading[2], `h${key}`)}
        </div>,
      );
      i++;
      continue;
    }

    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""));
        i++;
      }
      const ListTag = ordered ? "ol" : "ul";
      blocks.push(
        <ListTag
          key={key++}
          className={`my-1.5 space-y-1 pl-5 ${ordered ? "list-decimal" : "list-disc"} marker:text-zinc-400 dark:marker:text-zinc-500`}
        >
          {items.map((item, j) => (
            <li key={j}>{renderInline(item, `li${key}-${j}`)}</li>
          ))}
        </ListTag>,
      );
      continue;
    }

    if (line.trimStart().startsWith("> ")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].trimStart().startsWith("> ")) {
        quote.push(lines[i].trimStart().slice(2));
        i++;
      }
      blocks.push(
        <blockquote
          key={key++}
          className="my-2 border-l-2 border-teal-600/50 pl-3 text-zinc-600 dark:border-teal-400/50 dark:text-zinc-400"
        >
          {renderInline(quote.join(" "), `q${key}`)}
        </blockquote>,
      );
      continue;
    }

    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].trimStart().startsWith("```") &&
      !/^(#{1,4})\s+/.test(lines[i]) &&
      !/^\s*([-*]|\d+\.)\s+/.test(lines[i]) &&
      !lines[i].trimStart().startsWith("> ")
    ) {
      para.push(lines[i]);
      i++;
    }
    blocks.push(
      <p key={key++} className="my-1.5 first:mt-0 last:mb-0">
        {renderInline(para.join("\n"), `p${key}`)}
      </p>,
    );
  }

  return <div className="text-[14px] leading-relaxed break-words whitespace-pre-wrap [&_p]:whitespace-normal">{blocks}</div>;
}
