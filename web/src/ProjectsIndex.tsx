import { FormEvent, useMemo, useState } from "react";
import { api, Project } from "./api";
import { formatDue } from "./dates";
import Documents from "./Documents";
import { projectColor, relTime } from "./format";
import { Icon } from "./Icon";
import MemoryView from "./Memory";
import Reminders from "./Reminders";
import { Act, Section } from "./Work";

type Props = {
  projects: Project[];
  tab: Section;
  inbox: number;
  version: number;
  error: string;
  act: Act;
  onTab: (s: Section) => void;
  onOpen: (id: string) => void;
  onInboxChanged: () => void;
  onAuthError: (err: unknown) => void;
  onOpenConversation: (id: string) => void;
};

const TABS: [Section, string][] = [
  ["overview", "Projects"],
  ["memory", "Memory"],
  ["documents", "Documents"],
  ["reminders", "Reminders"],
];
const SEARCH_FROM = 7; // show the filter box once the grid stops fitting at a glance

// The Projects page with nothing open: every project as a card, plus memory, documents and reminders across all of them.
export default function ProjectsIndex({ projects, tab, inbox, version, error, act, onTab, onOpen, onInboxChanged, onAuthError, onOpenConversation }: Props) {
  // Tasks and Notes only exist inside a project.
  const shown: Section = tab === "tasks" || tab === "notes" ? "overview" : tab;
  const active = projects.filter((p) => p.status === "active");

  return (
    <section className="page projects-page">
      <header className="page-head">
        <div>
          <h2>Projects</h2>
          <p className="muted small">Group tasks, notes, memories and documents. In a chat focused on a project, pal sees its summary and open tasks.</p>
        </div>
      </header>

      <div className="subtabs" role="tablist" aria-label="Projects sections">
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={shown === id} onClick={() => onTab(id)}>
            {label}
            {id === "overview" && <span className="tab-count">{active.length}</span>}
            {id === "memory" && inbox > 0 && (
              <span className="tab-count hot" aria-label={`${inbox} suggestions waiting`}>
                {inbox}
              </span>
            )}
          </button>
        ))}
      </div>
      {shown !== "overview" && <p className="muted small scope-note">Across all projects. Open a project to see only its own.</p>}
      {error && <p className="error">{error}</p>}

      {shown === "memory" ? (
        <MemoryView embedded projectId={null} onOpenConversation={onOpenConversation} onInboxChanged={onInboxChanged} onAuthError={onAuthError} />
      ) : shown === "documents" ? (
        <Documents embedded projectId={null} onAuthError={onAuthError} />
      ) : shown === "reminders" ? (
        <Reminders version={version} projects={active} scope={null} act={act} onAuthError={onAuthError} />
      ) : (
        <ProjectGrid projects={projects} act={act} onOpen={onOpen} />
      )}
    </section>
  );
}

function ProjectGrid({ projects, act, onOpen }: { projects: Project[]; act: Act; onOpen: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);

  const { active, archived } = useMemo(() => {
    const q = query.trim().toLowerCase();
    const match = (p: Project) => !q || p.name.toLowerCase().includes(q) || p.summary.toLowerCase().includes(q);
    // Most recently touched first: the projects in play sit at the top.
    const recent = (a: Project, b: Project) => (b.last_activity ?? "").localeCompare(a.last_activity ?? "");
    return {
      active: projects.filter((p) => p.status === "active" && match(p)).sort(recent),
      archived: projects.filter((p) => p.status === "archived" && match(p)),
    };
  }, [projects, query]);
  const total = projects.filter((p) => p.status === "active").length;

  return (
    <>
      <div className="grid-bar">
        {total >= SEARCH_FROM && (
          <input className="search grid-search" placeholder="Filter projects" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Filter projects" />
        )}
        <span className="spacer" />
        {!adding && (
          <button className="primary" onClick={() => setAdding(true)}>
            <Icon name="plus" size={14} /> New project
          </button>
        )}
      </div>
      {adding && <NewProject act={act} onCreated={onOpen} onClose={() => setAdding(false)} />}

      {total === 0 && !adding ? (
        <div className="empty-state">
          <Icon name="work" size={28} />
          <p>No projects yet.</p>
          <p className="muted small">A project gathers the tasks, notes, memories and documents for one thing you're working on.</p>
          <button className="primary" onClick={() => setAdding(true)}>
            <Icon name="plus" size={14} /> Create your first project
          </button>
        </div>
      ) : (
        <div className="project-cards">
          {active.map((p) => (
            <ProjectCard key={p.id} p={p} onOpen={() => onOpen(p.id)} />
          ))}
          {active.length === 0 && total > 0 && <p className="muted small">No project matches “{query}”.</p>}
        </div>
      )}

      {archived.length > 0 && (
        <details className="archived">
          <summary>
            Archived <span className="tab-count">{archived.length}</span>
          </summary>
          <ul className="plain">
            {archived.map((p) => (
              <li key={p.id} className="archived-row">
                <button className="row-link" onClick={() => onOpen(p.id)}>
                  <span className="li-main">{p.name}</span>
                  {p.summary && <span className="li-meta">{p.summary}</span>}
                </button>
                <button className="small" onClick={() => act(() => api.editProject(p.id, { status: "active" }))}>
                  Restore
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

function ProjectCard({ p, onOpen }: { p: Project; onOpen: () => void }) {
  const open = p.open_tasks ?? 0;
  return (
    <button className="project-card" style={{ ["--swatch" as string]: projectColor(p.id) }} onClick={onOpen}>
      <span className="project-card-name">{p.name}</span>
      <span className={`project-summary small ${p.summary ? "" : "muted"}`}>{p.summary || "No summary yet."}</span>
      <span className="project-stats small">
        <span>{open === 0 ? "No open tasks" : `${open} open`}</span>
        {(p.overdue ?? 0) > 0 && <span className="stat-bad">{p.overdue} overdue</span>}
        {p.next_due && <span>next {formatDue(p.next_due)}</span>}
      </span>
      {p.last_activity && <span className="muted small">Active {relTime(p.last_activity)}</span>}
    </button>
  );
}

function NewProject({ act, onCreated, onClose }: { act: Act; onCreated: (id: string) => void; onClose: () => void }) {
  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");
  const add = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    act(async () => {
      const p = await api.addProject(name.trim(), summary.trim());
      onCreated(p.id);
    });
  };
  return (
    <form className="panel new-project" onSubmit={add} onKeyDown={(e) => e.key === "Escape" && onClose()}>
      <h3 className="panel-title">New project</h3>
      <input placeholder="Name, e.g. Kitchen renovation" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoFocus aria-label="Project name" />
      <textarea
        rows={2}
        placeholder="What's it about? (optional, pal reads this in chats focused on the project)"
        value={summary}
        onChange={(e) => setSummary(e.target.value)}
        maxLength={2000}
        aria-label="Summary"
      />
      <div className="form-actions">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button type="submit" className="primary" disabled={!name.trim()}>
          Create project
        </button>
      </div>
    </form>
  );
}
