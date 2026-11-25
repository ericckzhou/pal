import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, Memory, MemoryKind, MemoryPatch, Project } from "./api";
import ProjectSelect, { useProjects } from "./ProjectSelect";

const KINDS: MemoryKind[] = ["fact", "preference", "person", "project", "decision"];

type Props = {
  embedded?: boolean; // shown as a tab inside Projects
  projectId?: string | null; // only this project's saved memories
  onOpenConversation: (id: string) => void;
  onInboxChanged: () => void;
  onAuthError: (err: unknown) => void;
};

export default function MemoryView({ embedded = false, projectId = null, onOpenConversation, onInboxChanged, onAuthError }: Props) {
  const [tab, setTab] = useState<"proposed" | "confirmed">("proposed");
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
      {error && <p className="error">{error}</p>}

      <ul className="memory-list">
        {shown.length === 0 && (
          <li className="muted">{tab === "proposed" ? "No suggestions waiting." : "No memories yet. Try “remember that …” in a chat."}</li>
        )}
        {shown.map((m) => (
          <MemoryItem
            key={m.id}
            m={m}
            projects={projects}
            onSave={(patch) => act(() => api.editMemory(m.id, patch))}
            onConfirm={() => act(() => api.confirmMemory(m.id))}
            onDelete={() => act(() => api.deleteMemory(m.id))}
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
  onOpenConversation: (id: string) => void;
};

function MemoryItem({ m, projects, onSave, onConfirm, onDelete, onOpenConversation }: ItemProps) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(m.text);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const proposed = m.status === "proposed";

  return (
    <li className={`memory-item ${m.pinned ? "pinned" : ""}`}>
      {editing ? (
        <form
          className="memory-edit"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) onSave({ text: text.trim() });
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
      <div className="memory-meta small">
        <KindSelect value={m.kind} onChange={(kind) => onSave({ kind })} />
        <ProjectSelect value={m.project_id} projects={projects} onChange={(project_id) => onSave({ project_id })} />
        <span className={`badge ${m.origin}`} title={m.origin === "stated" ? "You said this" : "pal inferred this"}>
          {m.origin}
        </span>
        {m.source_conversation_id && (
          <button className="link" onClick={() => onOpenConversation(m.source_conversation_id!)}>
            source
          </button>
        )}
        <span className="spacer" />
        {proposed ? (
          <>
            <button className="primary small" onClick={onConfirm}>
              Accept
            </button>
            <button className="small" onClick={() => setEditing(true)}>
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
            <button className="small" onClick={() => setEditing(true)}>
              Edit
            </button>
            {confirmDelete ? (
              <>
                <button className="danger small" onClick={onDelete}>
                  Delete
                </button>
                <button className="small" onClick={() => setConfirmDelete(false)}>
                  Keep
                </button>
              </>
            ) : (
              <button className="small" onClick={() => setConfirmDelete(true)}>
                Delete
              </button>
            )}
          </>
        )}
      </div>
    </li>
  );
}
