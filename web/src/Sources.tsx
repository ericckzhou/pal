import { useState } from "react";
import { Source } from "./api";

export type SourceActions = {
  onOpenConversation: (id: string) => void;
  onOpenMemory: () => void;
};

const KIND_LABEL = { memory: "Memory", document: "Document", conversation: "Past chat", note: "Note", project: "Project", starcall: "Passage" } as const;
const kindLabel = (s: Source) => {
  if (s.kind === "memory" && s.pinned) return "Pinned memory";
  if (s.kind === "starcall" && s.starcall_kind === "concept") return "Concept";
  return KIND_LABEL[s.kind];
};

export const pageLabel = (start: number | null, end?: number | null) =>
  start == null ? "" : end == null || end === start ? `p.${start}` : `pp.${start}–${end}`;

export function citedHandles(text: string): Set<string> {
  return new Set(Array.from(text.matchAll(/\[([mdcnps]\d+)\]/g), (m) => m[1]));
}

function label(s: Source): string {
  if (s.kind === "memory") return s.snippet.length > 90 ? `${s.snippet.slice(0, 90)}…` : s.snippet;
  if (s.kind === "note") return `${s.title ?? "note"}: ${s.snippet.length > 80 ? `${s.snippet.slice(0, 80)}…` : s.snippet}`;
  if (s.kind === "project") return s.title ?? "Project";
  if (s.kind === "document") return `${s.title ?? "Document"}${s.page ? `, p.${s.page}` : ""}`;
  if (s.kind === "starcall") {
    const pages = pageLabel(s.page, s.page_end);
    return `${s.title ?? "StarcallOS"}${pages ? `, ${pages}` : ""}`;
  }
  return `${s.title ?? "Chat"}${s.at ? ` · ${s.at}` : ""}`;
}

/** The sources a reply cited, then (collapsed) anything else that was in context. */
export function SourceList(
  { sources, text, actions, anchor = "" }: { sources: Source[]; text: string; actions: SourceActions; anchor?: string },
) {
  const [showAll, setShowAll] = useState(false);
  const cited = citedHandles(text);
  const used = sources.filter((s) => cited.has(s.handle));
  const rest = sources.filter((s) => !cited.has(s.handle));
  const shown = showAll ? [...used, ...rest] : used;
  return (
    <div className="sources small">
      {shown.map((s) => (
        <SourceRow key={s.handle} s={s} actions={actions} anchor={anchor} />
      ))}
      {rest.length > 0 && (
        <button className="link small" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Hide" : `${rest.length} more in context, not cited`}
        </button>
      )}
    </div>
  );
}

function SourceRow({ s, actions, anchor }: { s: Source; actions: SourceActions; anchor: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="source" id={`src-${anchor}${s.handle}`}>
      <button className="link source-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="cite">{s.handle}</span>{" "}
        {s.kind === "starcall" && (
          <>
            <span className="src-tag" title="From your StarcallOS library">StarcallOS</span>{" "}
          </>
        )}
        {kindLabel(s)}: {label(s)}
      </button>
      {open && (
        <div className="source-body">
          {s.snippet ? (
            <p>{s.snippet}</p>
          ) : s.kind === "starcall" ? (
            <p className="muted">pal doesn't keep StarcallOS text. Open this page in StarcallOS to read it again.</p>
          ) : (
            <p>{s.snippet}</p>
          )}
          {s.kind === "conversation" && s.conversation_id && (
            <button className="small" onClick={() => actions.onOpenConversation(s.conversation_id!)}>
              Open chat
            </button>
          )}
          {s.kind === "memory" && (
            <button className="small" onClick={actions.onOpenMemory}>
              Open memory
            </button>
          )}
        </div>
      )}
    </div>
  );
}
