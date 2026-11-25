import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";
import { api, Note } from "./api";
import { Act } from "./Work";

type Props = { version: number; projectId: string; act: Act; onAuthError: (e: unknown) => void };
type Kind = "note" | "decision";
type Filter = "all" | Kind;

// Decisions and notes for one project. pal searches both when you chat; recent decisions go on the project card.
export default function ProjectNotes({ version, projectId, act, onAuthError }: Props) {
  const [items, setItems] = useState<Note[]>([]);
  const [text, setText] = useState("");
  const [kind, setKind] = useState<Kind>("decision");
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    api.notes(projectId).then(setItems).catch(onAuthError);
  }, [version, projectId, onAuthError]);

  const shown = useMemo(() => (filter === "all" ? items : items.filter((n) => n.kind === filter)), [items, filter]);
  const count = (k: Kind) => items.filter((n) => n.kind === k).length;

  const add = (e: FormEvent | KeyboardEvent<HTMLTextAreaElement>) => {
    e.preventDefault();
    if (!text.trim()) return;
    act(async () => {
      await api.addNote({ text: text.trim(), kind, project_id: projectId });
      setText("");
    });
  };

  return (
    <div className="tab-body">
      <form className="panel note-compose" onSubmit={add}>
        <div className="segmented" role="group" aria-label="Kind">
          <button type="button" aria-pressed={kind === "decision"} onClick={() => setKind("decision")}>
            Decision
          </button>
          <button type="button" aria-pressed={kind === "note"} onClick={() => setKind("note")}>
            Note
          </button>
        </div>
        <textarea
          rows={2}
          placeholder={kind === "decision" ? "What was decided, and why?" : "Write a note"}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) add(e);
          }}
          maxLength={5000}
          aria-label="Text"
        />
        <div className="form-actions">
          <span className="muted small">Ctrl+Enter to save</span>
          <button type="submit" className="primary" disabled={!text.trim()}>
            Save {kind}
          </button>
        </div>
      </form>

      {items.length > 0 && (
        <div className="label-bar" role="toolbar" aria-label="Filter">
          <button className={`chip ${filter === "all" ? "active" : ""}`} aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
            All <span className="chip-count">{items.length}</span>
          </button>
          <button className={`chip ${filter === "decision" ? "active" : ""}`} aria-pressed={filter === "decision"} onClick={() => setFilter("decision")}>
            Decisions <span className="chip-count">{count("decision")}</span>
          </button>
          <button className={`chip ${filter === "note" ? "active" : ""}`} aria-pressed={filter === "note"} onClick={() => setFilter("note")}>
            Notes <span className="chip-count">{count("note")}</span>
          </button>
        </div>
      )}

      <ul className="memory-list">
        {shown.length === 0 && <li className="muted small empty-row">Nothing here yet. pal searches decisions and notes when you chat.</li>}
        {shown.map((n) => (
          <NoteRow key={n.id} n={n} act={act} />
        ))}
      </ul>
    </div>
  );
}

function NoteRow({ n, act }: { n: Note; act: Act }) {
  const [confirm, setConfirm] = useState(false);
  return (
    <li className={`memory-item note-item ${n.kind}`}>
      <p className="memory-text pre">{n.text}</p>
      <div className="memory-meta small">
        <span className={`badge ${n.kind === "decision" ? "stated" : ""}`}>{n.kind}</span>
        <span className="muted">{new Date(n.created_at).toLocaleDateString()}</span>
        <span className="spacer" />
        {confirm ? (
          <>
            <button className="danger small" onClick={() => act(() => api.deleteNote(n.id))}>
              Delete
            </button>
            <button className="small" onClick={() => setConfirm(false)}>
              Keep
            </button>
          </>
        ) : (
          <button className="small" onClick={() => setConfirm(true)}>
            Delete
          </button>
        )}
      </div>
    </li>
  );
}
