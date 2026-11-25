import { FormEvent, useEffect, useState } from "react";
import { api, Task } from "./api";
import { formatDue, today } from "./dates";
import { Act } from "./Work";

type Props = { version: number; projectId: string; act: Act; onAuthError: (e: unknown) => void; onBoard: () => void };
type Filter = "open" | "done";

// The project's tasks as a checklist. The board (all projects, three columns) is one click away.
export default function ProjectTasks({ version, projectId, act, onAuthError, onBoard }: Props) {
  const [items, setItems] = useState<Task[]>([]);
  const [filter, setFilter] = useState<Filter>("open");
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");

  useEffect(() => {
    api.tasks({ status: filter, project_id: projectId }).then(setItems).catch(onAuthError);
  }, [version, projectId, filter, onAuthError]);

  const add = (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    act(async () => {
      await api.addTask({ title: title.trim(), project_id: projectId, due_date: due || null });
      setTitle("");
      setDue("");
    });
  };
  const now = today();

  return (
    <div className="tab-body">
      <form className="memory-add" onSubmit={add}>
        <input placeholder="Add a task" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} aria-label="New task" />
        <input type="date" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due date" className="date" />
        <button type="submit" className="primary" disabled={!title.trim()}>
          Add
        </button>
      </form>

      <div className="list-bar">
        <div className="segmented" role="group" aria-label="Show">
          <button aria-pressed={filter === "open"} onClick={() => setFilter("open")}>
            Open
          </button>
          <button aria-pressed={filter === "done"} onClick={() => setFilter("done")}>
            Done
          </button>
        </div>
        <span className="spacer" />
        <button className="link small" onClick={onBoard}>
          Open the task board →
        </button>
      </div>

      <ul className="memory-list">
        {items.length === 0 && (
          <li className="muted small empty-row">{filter === "open" ? "No open tasks. Add one above, or type /task in a chat." : "Nothing done yet."}</li>
        )}
        {items.map((t) => (
          <TaskRow key={t.id} t={t} now={now} act={act} />
        ))}
      </ul>
    </div>
  );
}

function TaskRow({ t, now, act }: { t: Task; now: string; act: Act }) {
  const [confirm, setConfirm] = useState(false);
  const done = t.status === "done";
  return (
    <li className={`task ${t.status}`}>
      <input
        type="checkbox"
        checked={done}
        onChange={() => act(() => api.editTask(t.id, { status: done ? "open" : "done" }))}
        aria-label={`Mark ${t.title} ${done ? "not done" : "done"}`}
      />
      <span className="task-title">{t.title}</span>
      {!done && t.stage === "doing" && <span className="badge stated">in progress</span>}
      {t.due_date && <span className={`badge ${!done && t.due_date < now ? "overdue" : ""}`}>{formatDue(t.due_date, now)}</span>}
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
    </li>
  );
}
