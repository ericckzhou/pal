import { useState } from "react";
import { api, Project, Proposal } from "./api";
import { REPEATS } from "./dates";

type Props = {
  proposal: Proposal;
  projects: Project[];
  onDone: (message: string) => void;
  onError: (err: unknown) => void;
};

/** A task or reminder pal drafted. Nothing is created until the owner presses Add (S6). */
export function ProposalCard({ proposal, projects, onDone, onError }: Props) {
  const p = proposal.payload;
  const isTask = proposal.action === "create_task";
  const [title, setTitle] = useState(p.title);
  const [due, setDue] = useState(p.due_date ?? "");
  const [at, setAt] = useState(p.at ?? "");
  const [rrule, setRrule] = useState(p.rrule ?? "");
  const [projectId, setProjectId] = useState(p.project_id ?? "");
  const [busy, setBusy] = useState(false);
  const repeats = REPEATS.some(([v]) => v === rrule) ? REPEATS : [...REPEATS, [rrule, p.repeat || rrule] as [string, string]];

  const run = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true);
    try {
      await fn();
      onDone(message);
    } catch (err) {
      onError(err);
      setBusy(false);
    }
  };

  const confirm = () =>
    run(
      () =>
        api.confirmProposal(proposal.id, {
          title,
          project_id: projectId || null,
          ...(isTask ? { due_date: due || null } : { at, rrule: rrule || null }),
        }),
      isTask ? `Task added: “${title}”` : `Reminder set: “${title}”`,
    );

  return (
    <div className="proposal" role="group" aria-label={isTask ? "Suggested task" : "Suggested reminder"}>
      <div className="small muted">{isTask ? "Add this task?" : "Set this reminder?"}</div>
      <div className="memory-add">
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} aria-label="Title" />
        {isTask ? (
          <input type="date" className="date" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due date" />
        ) : (
          <>
            <input type="datetime-local" className="date" value={at} onChange={(e) => setAt(e.target.value)} aria-label="When" />
            <select value={rrule} onChange={(e) => setRrule(e.target.value)} aria-label="Repeat">
              {repeats.map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </>
        )}
        {projects.length > 0 && (
          <select value={projectId} onChange={(e) => setProjectId(e.target.value)} aria-label="Project">
            <option value="">No project</option>
            {projects.map((pr) => (
              <option key={pr.id} value={pr.id}>
                {pr.name}
              </option>
            ))}
          </select>
        )}
      </div>
      <div className="memory-meta">
        <button className="primary small" disabled={busy || !title.trim() || (!isTask && !at)} onClick={confirm}>
          {isTask ? "Add task" : "Set reminder"}
        </button>
        <button className="small" disabled={busy} onClick={() => run(() => api.rejectProposal(proposal.id), "Dismissed.")}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
