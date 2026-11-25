import { DragEvent, FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, Project, Task, TaskStage } from "./api";
import ProjectSelect from "./ProjectSelect";
import { today } from "./dates";
import { projectHue as hue } from "./format";

type Props = { version: number; onAuthError: (err: unknown) => void };
type Column = "todo" | "doing" | "done";
type Label = "all" | "none" | string; // a project id

const COLUMNS: { id: Column; title: string }[] = [
  { id: "todo", title: "To do" },
  { id: "doing", title: "In progress" },
  { id: "done", title: "Done" },
];
const DONE_DAYS = 14; // older done tasks drop off the board (they stay in the weekly digest)
const LABEL_KEY = "pal.board.label";

const columnOf = (t: Task): Column => (t.status === "done" ? "done" : t.stage);
const patchFor = (col: Column): { status: "open" | "done"; stage?: TaskStage } =>
  col === "done" ? { status: "done" } : { status: "open", stage: col };

function formatDue(d: string): string {
  return new Date(`${d}T00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function loadLabel(): Label {
  try {
    return localStorage.getItem(LABEL_KEY) || "all";
  } catch {
    return "all";
  }
}

export default function TaskBoard({ version, onAuthError }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [label, setLabelState] = useState<Label>(loadLabel);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [bump, setBump] = useState(0);
  const [dragOver, setDragOver] = useState<Column | null>(null);

  const setLabel = (l: Label) => {
    setLabelState(l);
    try {
      localStorage.setItem(LABEL_KEY, l);
    } catch {
      /* storage blocked */
    }
  };

  useEffect(() => {
    api.projects().then(setProjects).catch(onAuthError);
    api.tasks({ status: "all" }).then(setTasks).catch(onAuthError);
  }, [version, bump, onAuthError]);

  // A remembered label for a project that's since been archived or deleted falls back to all.
  const shownLabel: Label = label === "all" || label === "none" || projects.some((p) => p.id === label) || !projects.length ? label : "all";

  const act = useCallback(
    async (fn: () => Promise<unknown>) => {
      setError("");
      try {
        await fn();
      } catch (err) {
        onAuthError(err);
        setError(err instanceof Error ? err.message : "Something went wrong.");
      } finally {
        setBump((b) => b + 1);
      }
    },
    [onAuthError],
  );

  const move = (t: Task, col: Column) => {
    if (columnOf(t) === col) return;
    // Show the move at once; the reload after the call settles it either way.
    const p = patchFor(col);
    setTasks((all) => all.map((x) => (x.id === t.id ? { ...x, status: p.status, stage: p.stage ?? x.stage } : x)));
    act(() => api.editTask(t.id, p));
  };

  const visible = useMemo(() => {
    const cutoff = Date.now() - DONE_DAYS * 86_400_000;
    const q = query.trim().toLowerCase();
    return tasks.filter(
      (t) =>
        (shownLabel === "all" || (shownLabel === "none" ? t.project_id === null : t.project_id === shownLabel)) &&
        (!q || t.title.toLowerCase().includes(q)) &&
        (t.status !== "done" || !t.done_at || new Date(t.done_at).getTime() >= cutoff),
    );
  }, [tasks, shownLabel, query]);

  const counts = useMemo(() => {
    const open = tasks.filter((t) => t.status === "open");
    const m = new Map<string, number>();
    for (const t of open) m.set(t.project_id ?? "none", (m.get(t.project_id ?? "none") ?? 0) + 1);
    return { all: open.length, by: m };
  }, [tasks]);

  const onDrop = (e: DragEvent, col: Column) => {
    e.preventDefault();
    setDragOver(null);
    const t = tasks.find((x) => x.id === e.dataTransfer.getData("text/plain"));
    if (t) move(t, col);
  };

  return (
    <section className="memory board-page">
      <header className="memory-head">
        <h2>Tasks</h2>
        <input className="search board-search" placeholder="Filter cards" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Filter cards" />
      </header>

      <div className="label-bar" role="toolbar" aria-label="Project labels">
        <LabelChip active={shownLabel === "all"} onClick={() => setLabel("all")} text="All" count={counts.all} />
        {projects.map((p) => (
          <LabelChip key={p.id} active={shownLabel === p.id} onClick={() => setLabel(p.id)} text={p.name} count={counts.by.get(p.id) ?? 0} hue={hue(p.id)} />
        ))}
        <LabelChip active={shownLabel === "none"} onClick={() => setLabel("none")} text="No project" count={counts.by.get("none") ?? 0} />
      </div>

      <AddCard projects={projects} label={shownLabel} act={act} />
      {error && <p className="error">{error}</p>}

      <div className="board">
        {COLUMNS.map((c) => {
          const items = visible.filter((t) => columnOf(t) === c.id);
          return (
            <section
              key={c.id}
              className={`board-col ${dragOver === c.id ? "drag-over" : ""}`}
              aria-label={c.title}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(c.id);
              }}
              onDragLeave={() => setDragOver((d) => (d === c.id ? null : d))}
              onDrop={(e) => onDrop(e, c.id)}
            >
              <h3 className="board-col-head">
                {c.title} <span className="count-pill">{items.length}</span>
              </h3>
              <ul className="board-cards">
                {items.length === 0 && <li className="muted small board-empty">{c.id === "done" ? `Nothing done in the last ${DONE_DAYS} days.` : "Drop cards here."}</li>}
                {items.map((t) => (
                  <Card key={t.id} t={t} col={c.id} projects={projects} showLabel={shownLabel === "all"} onMove={(col) => move(t, col)} act={act} />
                ))}
              </ul>
            </section>
          );
        })}
      </div>
    </section>
  );
}

function LabelChip({ active, onClick, text, count, hue: h }: { active: boolean; onClick: () => void; text: string; count: number; hue?: number }) {
  return (
    <button className={`chip ${active ? "active" : ""}`} aria-pressed={active} onClick={onClick}>
      {h !== undefined && <span className="chip-dot" style={{ background: `hsl(${h} 55% 50%)` }} aria-hidden="true" />}
      {text}
      <span className="chip-count">{count}</span>
    </button>
  );
}

type Act = (fn: () => Promise<unknown>) => Promise<void>;

function AddCard({ projects, label, act }: { projects: Project[]; label: Label; act: Act }) {
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [project, setProject] = useState<string | null>(null);
  // The selected label is the default project for new cards.
  useEffect(() => setProject(label === "all" || label === "none" ? null : label), [label]);

  const add = (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    act(async () => {
      await api.addTask({ title: title.trim(), project_id: project, due_date: due || null });
      setTitle("");
      setDue("");
    });
  };
  return (
    <form className="memory-add" onSubmit={add}>
      <input placeholder="Add a card to To do" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} />
      <ProjectSelect value={project} projects={projects} onChange={setProject} />
      <input type="date" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due date" className="date" />
      <button type="submit" className="primary" disabled={!title.trim()}>
        Add
      </button>
    </form>
  );
}

type CardProps = { t: Task; col: Column; projects: Project[]; showLabel: boolean; onMove: (c: Column) => void; act: Act };

function Card({ t, col, projects, showLabel, onMove, act }: CardProps) {
  const [confirm, setConfirm] = useState(false);
  const [editing, setEditing] = useState(false);
  if (editing) return <CardEditor t={t} projects={projects} act={act} onClose={() => setEditing(false)} />;
  const idx = COLUMNS.findIndex((c) => c.id === col);
  const prev = COLUMNS[idx - 1];
  const next = COLUMNS[idx + 1];
  const overdue = t.status === "open" && t.due_date !== null && t.due_date < today();
  return (
    <li
      className={`board-card ${t.status}`}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("text/plain", t.id);
        e.dataTransfer.effectAllowed = "move";
      }}
    >
      <button className="board-card-title" onClick={() => setEditing(true)} title="Edit title, project or due date">
        {t.title}
      </button>
      <div className="board-card-meta small">
        {showLabel && t.project && t.project_id && (
          <span className="label-tag" style={{ borderColor: `hsl(${hue(t.project_id)} 55% 50%)` }}>
            {t.project}
          </span>
        )}
        {t.due_date && <span className={`badge ${overdue ? "overdue" : ""}`}>{formatDue(t.due_date)}</span>}
        <span className="spacer" />
        {/* Buttons as well as drag: works with a keyboard and on phones. */}
        {prev && (
          <button className="icon" aria-label={`Move ${t.title} to ${prev.title}`} title={`Move to ${prev.title}`} onClick={() => onMove(prev.id)}>
            ←
          </button>
        )}
        {next && (
          <button className="icon" aria-label={`Move ${t.title} to ${next.title}`} title={`Move to ${next.title}`} onClick={() => onMove(next.id)}>
            →
          </button>
        )}
        {confirm ? (
          <>
            <button className="danger small" onClick={() => act(() => api.deleteTask(t.id))}>
              Delete
            </button>
            <button className="small" onClick={() => setConfirm(false)}>
              Keep
            </button>
          </>
        ) : (
          <button className="icon" aria-label={`Delete ${t.title}`} onClick={() => setConfirm(true)}>
            ×
          </button>
        )}
      </div>
    </li>
  );
}

function CardEditor({ t, projects, act, onClose }: { t: Task; projects: Project[]; act: Act; onClose: () => void }) {
  const [title, setTitle] = useState(t.title);
  const [project, setProject] = useState<string | null>(t.project_id);
  const [due, setDue] = useState(t.due_date ?? "");
  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    act(() => api.editTask(t.id, { title: title.trim(), project_id: project, due_date: due || null }));
    onClose();
  };
  return (
    <li className="board-card editing">
      <form className="board-edit" onSubmit={save} onKeyDown={(e) => e.key === "Escape" && onClose()}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} autoFocus aria-label="Title" />
        <ProjectSelect value={project} projects={projects} onChange={setProject} />
        <input type="date" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due date" className="date" />
        <div className="board-card-meta">
          <span className="spacer" />
          <button type="button" className="small" onClick={onClose}>
            Cancel
          </button>
          <button className="primary small" disabled={!title.trim()}>
            Save
          </button>
        </div>
      </form>
    </li>
  );
}
