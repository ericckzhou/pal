import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, Memory, MemoryKind, MemoryPatch, MemoryStatus, Project } from "./api";
import ProjectSelect, { useProjects } from "./ProjectSelect";

const KINDS: MemoryKind[] = ["fact", "preference", "person", "project", "decision"];
const EMPTY: Record<MemoryStatus, string> = {
  proposed: "No suggestions waiting.",
  confirmed: "No memories yet. Try “remember that …” in a chat.",
  replaced: "Nothing replaced yet.",
};

type Props = {
  embedded?: boolean; // shown as a tab inside Projects
  projectId?: string | null; // only this project's saved memories
  onOpenConversation: (id: string) => void;
  onInboxChanged: () => void;
  onAuthError: (err: unknown) => void;
};

export default function MemoryView({ embedded = false, projectId = null, onOpenConversation, onInboxChanged, onAuthError }: Props) {
  const [tab, setTab] = useState<MemoryStatus>("proposed");
  const [items, setItems] = useState<Memory[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const projects = useProjects();

  const load = useCallback(() => {
    api.memories(tab, query).then(setItems).catch(onAuthError);
  }, [tab, query, onAuthError]);

  useEffect(() => {
    const t = setTimeout(load, query ? 250 : 0);
    return () => clearTimeout(t);
  }, [load, query]);

  const act = async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
      load();
      onInboxChanged();
    } catch (err) {
      onAuthError(err);
      setError(err instanceof Error ? err.message : "Something went wrong.");
    }
  };

  // Suggestions are rarely tagged yet, so the inbox always shows them all; saved memories follow the project.
  const shown = projectId && tab === "confirmed" ? items.filter((m) => m.project_id === projectId) : items;

  return (
    <section className={embedded ? "embedded" : "memory"}>
      <header className="memory-head">
        {!embedded && <h2>Memory</h2>}
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "proposed"} onClick={() => setTab("proposed")}>
            Inbox
          </button>
          <button role="tab" aria-selected={tab === "confirmed"} onClick={() => setTab("confirmed")}>
            Saved
          </button>
          <button role="tab" aria-selected={tab === "replaced"} onClick={() => setTab("replaced")}>
            Replaced
          </button>
        </div>
      </header>

      {tab === "confirmed" && (
        <>
          <AddMemory key={projectId ?? ""} projects={projects} initialProject={projectId} onAdd={(m) => act(() => api.addMemory(m))} />
          <input className="search" placeholder="Search memories" value={query} onChange={(e) => setQuery(e.target.value)} />
        </>
      )}
      {tab === "proposed" && (
        <p className="muted small">
          pal suggests these after a conversation goes quiet. Nothing here is used until you accept it.
        </p>
      )}
      {tab === "replaced" && (
        <p className="muted small">Out-of-date memories. pal no longer uses them; restore one to put it back.</p>
      )}
      {error && <p className="error">{error}</p>}

      <ul className="memory-list">
        {shown.length === 0 && (
          <li className="muted">{EMPTY[tab]}</li>
        )}
        {shown.map((m) => (
          <MemoryItem
            key={m.id}
            m={m}
            projects={projects}
            onSave={(patch) => act(() => api.editMemory(m.id, patch))}
            onConfirm={() => act(() => api.confirmMemory(m.id))}
            onDelete={() => act(() => api.deleteMemory(m.id))}
            onReplace={() => m.replaces_id && act(() => api.replaceMemory(m.id, m.replaces_id!))}
            onKeepBoth={() => act(() => api.keepBothMemories(m.id))}
            onReplaceWith={(text) => act(() => api.replaceMemoryWith(m.id, text))}
            onRestore={() => act(() => api.restoreMemory(m.id))}
            onOpenConversation={onOpenConversation}
          />
        ))}
      </ul>
    </section>
  );
}

type NewMemory = { text: string; kind: MemoryKind; pinned: boolean; project_id: string | null };

function AddMemory({ projects, initialProject, onAdd }: { projects: Project[]; initialProject: string | null; onAdd: (m: NewMemory) => void }) {
  const [text, setText] = useState("");
  const [kind, setKind] = useState<MemoryKind>("fact");
  const [project, setProject] = useState<string | null>(initialProject);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    onAdd({ text: text.trim(), kind, pinned: false, project_id: project });
    setText("");
  };
  return (
    <form className="memory-add" onSubmit={submit}>
      <input placeholder="Add something pal should know" value={text} maxLength={500} onChange={(e) => setText(e.target.value)} />
      <KindSelect value={kind} onChange={setKind} />
      <ProjectSelect value={project} projects={projects} onChange={setProject} />
      <button className="primary" disabled={!text.trim()}>
        Add
      </button>
    </form>
  );
}

function KindSelect({ value, onChange }: { value: MemoryKind; onChange: (k: MemoryKind) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as MemoryKind)} aria-label="Kind">
      {KINDS.map((k) => (
        <option key={k}>{k}</option>
      ))}
    </select>
  );
}

type ItemProps = {
  m: Memory;
  projects: Project[];
  onSave: (patch: MemoryPatch) => void;
  onConfirm: () => void;
  onDelete: () => void;
  onReplace: () => void;
  onKeepBoth: () => void;
  onReplaceWith: (text: string) => void;
  onRestore: () => void;
  onOpenConversation: (id: string) => void;
};

function MemoryItem(props: ItemProps) {
  const { m, projects, onSave, onConfirm, onDelete, onReplaceWith, onRestore, onOpenConversation } = props;
  // "edit" fixes this memory in place; "update" saves a new version and moves this one to Replaced.
  const [editing, setEditing] = useState<false | "edit" | "update">(false);
  const [text, setText] = useState(m.text);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const proposed = m.status === "proposed";
  const replaced = m.status === "replaced";

  return (
    <li className={`memory-item ${m.pinned ? "pinned" : ""}`}>
      {editing ? (
        <form
          className="memory-edit"
          onSubmit={(e) => {
            e.preventDefault();
            const t = text.trim();
            if (t && editing === "update" && t !== m.text) onReplaceWith(t);
            else if (t && editing === "edit") onSave({ text: t });
            setText(m.text);
            setEditing(false);
          }}
        >
          <input value={text} maxLength={500} autoFocus onChange={(e) => setText(e.target.value)} />
          <button className="primary small">Save</button>
          <button type="button" className="small" onClick={() => (setText(m.text), setEditing(false))}>
            Cancel
          </button>
        </form>
      ) : (
        <p className="memory-text">{m.text}</p>
      )}
      {editing === "update" && <p className="muted small">The current text moves to Replaced, where you can restore it.</p>}
      <ReplaceLink {...props} />
      <div className="memory-meta small">
        {!replaced && <KindSelect value={m.kind} onChange={(kind) => onSave({ kind })} />}
        {!replaced && (
          <ProjectSelect value={m.project_id} projects={projects} onChange={(project_id) => onSave({ project_id })} />
        )}
        <span className={`badge ${m.origin}`} title={m.origin === "stated" ? "You said this" : "pal inferred this"}>
          {m.origin}
        </span>
        {m.source_conversation_id && (
          <button className="link" onClick={() => onOpenConversation(m.source_conversation_id!)}>
            source
          </button>
        )}
        <span className="spacer" />
        {replaced ? (
          <>
            <button className="small" onClick={onRestore} title="Use this memory again">
              Restore
            </button>
            <DeleteButton confirming={confirmDelete} setConfirming={setConfirmDelete} onDelete={onDelete} />
          </>
        ) : proposed ? (
          <>
            <button className="primary small" onClick={onConfirm}>
              Accept
            </button>
            <button className="small" onClick={() => setEditing("edit")}>
              Edit
            </button>
            <button className="small" onClick={onDelete}>
              Reject
            </button>
          </>
        ) : (
          <>
            <button className="small" aria-pressed={m.pinned} onClick={() => onSave({ pinned: !m.pinned })}
              title="Pinned memories are in every conversation">
              {m.pinned ? "Unpin" : "Pin"}
            </button>
            <button className="small" onClick={() => setEditing("edit")} title="Fix this memory in place">
              Edit
            </button>
            <button className="small" onClick={() => setEditing("update")}
              title="It changed: save a new version and keep this one under Replaced">
              Update
            </button>
            <DeleteButton confirming={confirmDelete} setConfirming={setConfirmDelete} onDelete={onDelete} />
          </>
        )}
      </div>
    </li>
  );
}

function DeleteButton({ confirming, setConfirming, onDelete }: {
  confirming: boolean;
  setConfirming: (v: boolean) => void;
  onDelete: () => void;
}) {
  return confirming ? (
    <>
      <button className="danger small" onClick={onDelete}>
        Delete
      </button>
      <button className="small" onClick={() => setConfirming(false)}>
        Keep
      </button>
    </>
  ) : (
    <button className="small" onClick={() => setConfirming(true)}>
      Delete
    </button>
  );
}

/** What this memory replaces (or might), or what replaced it. */
function ReplaceLink({ m, onReplace, onKeepBoth }: ItemProps) {
  if (m.status === "replaced") {
    return m.replaced_by_text ? <p className="memory-link muted small">Replaced by “{m.replaced_by_text}”</p> : null;
  }
  if (!m.replaces_text) return null;
  if (m.replace_state === "applied") {
    return <p className="memory-link muted small">Replaces “{m.replaces_text}”</p>;
  }
  return (
    <div className="memory-link suggestion small">
      <span>May make this out of date: “{m.replaces_text}”</span>
      <span className="spacer" />
      <button className="primary small" onClick={onReplace}
        title={m.status === "proposed" ? "Accept this and retire the older memory" : "Retire the older memory"}>
        {m.status === "proposed" ? "Accept & replace" : "Replace"}
      </button>
      <button className="small" onClick={onKeepBoth} title="Both are true; keep using both">
        Keep both
      </button>
    </div>
  );
}
