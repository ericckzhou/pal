import { FormEvent, useEffect, useRef, useState } from "react";
import { api, Project } from "./api";
import Documents from "./Documents";
import { projectColor } from "./format";
import { Icon } from "./Icon";
import MemoryView from "./Memory";
import ProjectNotes from "./ProjectNotes";
import ProjectOverview from "./ProjectOverview";
import ProjectTasks from "./ProjectTasks";
import Reminders from "./Reminders";
import { View } from "./Sidebar";
import { Act, Section } from "./Work";

type Props = {
  project: Project;
  projects: Project[];
  tab: Section;
  inbox: number;
  version: number;
  error: string;
  act: Act;
  onTab: (s: Section) => void;
  onBack: () => void;
  onView: (v: View) => void;
  onInboxChanged: () => void;
  onAuthError: (err: unknown) => void;
  onOpenConversation: (id: string) => void;
};

const TABS: [Section, string][] = [
  ["overview", "Overview"],
  ["tasks", "Tasks"],
  ["notes", "Notes"],
  ["memory", "Memory"],
  ["documents", "Documents"],
  ["reminders", "Reminders"],
];

// One project: who it is (name, summary), then its six tabs, each scoped to it.
export default function ProjectDetail(props: Props) {
  const { project, projects, tab, inbox, version, error, act, onTab, onBack, onView, onInboxChanged, onAuthError, onOpenConversation } = props;
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const archived = project.status === "archived";

  return (
    <section className="page project-page" style={{ ["--swatch" as string]: projectColor(project.id) }}>
      <button className="link back-link" onClick={onBack}>
        <Icon name="back" size={14} /> All projects
      </button>

      {editing ? (
        <EditProject project={project} act={act} onClose={() => setEditing(false)} />
      ) : (
        <header className="project-hero">
          <span className="project-swatch" aria-hidden="true" />
          <div className="hero-text">
            <h2>
              {project.name}
              {archived && <span className="badge">archived</span>}
            </h2>
            {project.summary ? (
              <p className="hero-summary pre">{project.summary}</p>
            ) : (
              <button className="link hero-summary-empty" onClick={() => setEditing(true)}>
                Add a summary so pal knows what this project is about
              </button>
            )}
          </div>
          <div className="hero-actions">
            <button className="small" onClick={() => setEditing(true)}>
              <Icon name="edit" size={14} /> Edit
            </button>
            <ProjectMenu
              archived={archived}
              onArchive={() => act(() => api.editProject(project.id, { status: archived ? "active" : "archived" }))}
              onDelete={() => setConfirmDelete(true)}
            />
          </div>
        </header>
      )}

      {confirmDelete && (
        <div className="alert confirm-strip" role="alert">
          <span>Delete “{project.name}”? Its tasks, notes, memories and documents stay, just outside any project.</span>
          <span className="form-actions">
            <button className="small" onClick={() => setConfirmDelete(false)}>
              Keep
            </button>
            <button className="danger small" onClick={() => act(async () => { await api.deleteProject(project.id); onBack(); })}>
              Delete project
            </button>
          </span>
        </div>
      )}
      {error && <p className="error">{error}</p>}

      <div className="subtabs" role="tablist" aria-label={`${project.name} sections`}>
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} onClick={() => onTab(id)}>
            {label}
            {id === "tasks" && (project.open_tasks ?? 0) > 0 && (
              <span className={`tab-count ${(project.overdue ?? 0) > 0 ? "bad" : ""}`}>{project.open_tasks}</span>
            )}
            {id === "memory" && inbox > 0 && (
              <span className="tab-count hot" aria-label={`${inbox} suggestions waiting`}>
                {inbox}
              </span>
            )}
          </button>
        ))}
      </div>

      {tab === "tasks" ? (
        <ProjectTasks version={version} projectId={project.id} act={act} onAuthError={onAuthError} onBoard={() => onView("tasks")} />
      ) : tab === "notes" ? (
        <ProjectNotes version={version} projectId={project.id} act={act} onAuthError={onAuthError} />
      ) : tab === "memory" ? (
        <MemoryView embedded projectId={project.id} onOpenConversation={onOpenConversation} onInboxChanged={onInboxChanged} onAuthError={onAuthError} />
      ) : tab === "documents" ? (
        <Documents embedded projectId={project.id} onAuthError={onAuthError} />
      ) : tab === "reminders" ? (
        <Reminders version={version} projects={projects} scope={project.id} act={act} onAuthError={onAuthError} />
      ) : (
        <ProjectOverview projectId={project.id} version={version} act={act} onTab={onTab} onOpenConversation={onOpenConversation} onAuthError={onAuthError} />
      )}
    </section>
  );
}

function EditProject({ project, act, onClose }: { project: Project; act: Act; onClose: () => void }) {
  const [name, setName] = useState(project.name);
  const [summary, setSummary] = useState(project.summary);
  const changed = name.trim() !== project.name || summary !== project.summary;
  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    act(async () => {
      await api.editProject(project.id, { name: name.trim(), summary });
      onClose();
    });
  };
  return (
    <form className="panel project-edit" onSubmit={save} onKeyDown={(e) => e.key === "Escape" && onClose()}>
      <label className="field">
        <span className="field-label">Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoFocus />
      </label>
      <label className="field">
        <span className="field-label">Summary</span>
        <textarea rows={3} value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={2000} />
        <span className="muted small">pal sees this, with open tasks and recent decisions, in chats focused on this project.</span>
      </label>
      <div className="form-actions">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button type="submit" className="primary" disabled={!name.trim() || !changed}>
          Save
        </button>
      </div>
    </form>
  );
}

// A small overflow menu for the rarer actions; closes on Escape, an outside click, or a choice.
function ProjectMenu({ archived, onArchive, onDelete }: { archived: boolean; onArchive: () => void; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);
  const choose = (fn: () => void) => () => {
    setOpen(false);
    fn();
  };
  return (
    <div className="menu-wrap" ref={ref} onKeyDown={(e) => e.key === "Escape" && setOpen(false)}>
      <button className="small icon-only" aria-label="More project actions" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Icon name="more" size={16} />
      </button>
      {open && (
        <div className="menu" role="menu">
          <button role="menuitem" onClick={choose(onArchive)} autoFocus>
            <Icon name="archive" size={14} /> {archived ? "Restore from archive" : "Archive"}
          </button>
          <button role="menuitem" className="menu-danger" onClick={choose(onDelete)}>
            <Icon name="trash" size={14} /> Delete…
          </button>
        </div>
      )}
    </div>
  );
}
