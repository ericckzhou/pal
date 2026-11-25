import { useCallback, useEffect, useState } from "react";
import { api, Project } from "./api";
import ProjectDetail from "./ProjectDetail";
import ProjectsIndex from "./ProjectsIndex";
import { View } from "./Sidebar";

// Tabs of the Projects page. With no project open, Overview is the project list and the other three span
// every project; with one open, all six are scoped to it.
export type Section = "overview" | "tasks" | "notes" | "memory" | "documents" | "reminders";
export type Act = (fn: () => Promise<unknown>) => Promise<void>;

type Props = {
  version: number;
  section: Section;
  inbox: number;
  onSection: (s: Section) => void;
  onInboxChanged: () => void;
  onAuthError: (err: unknown) => void;
  onOpenConversation: (id: string) => void;
  onView: (v: View) => void;
};

const PROJECT_KEY = "pal.project";

function loadProject(): string {
  try {
    return localStorage.getItem(PROJECT_KEY) ?? "";
  } catch {
    return "";
  }
}

export default function Work({ version, section, inbox, onSection, onInboxChanged, onAuthError, onOpenConversation, onView }: Props) {
  const [projects, setProjects] = useState<Project[] | null>(null); // null until the first load
  const [selected, setSelectedState] = useState<string>(loadProject); // "" = the project list
  const [error, setError] = useState("");
  const [bump, setBump] = useState(0);

  const setSelected = (id: string) => {
    setSelectedState(id);
    try {
      localStorage.setItem(PROJECT_KEY, id);
    } catch {
      /* storage blocked */
    }
  };

  useEffect(() => {
    api.projects(true).then(setProjects).catch(onAuthError);
  }, [version, bump, onAuthError]);

  const act: Act = useCallback(
    async (fn) => {
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

  if (projects === null) return <section className="page"><p className="muted small">Loading projects…</p></section>;

  // A remembered project that's since been deleted falls back to the list.
  const project = projects.find((p) => p.id === selected) ?? null;
  const openProject = (id: string) => {
    setSelected(id);
    onSection("overview");
  };
  const shared = { version: version + bump, inbox, act, onAuthError, onInboxChanged, onOpenConversation };

  return project ? (
    <ProjectDetail
      key={project.id}
      {...shared}
      project={project}
      projects={projects}
      tab={section}
      error={error}
      onTab={onSection}
      onBack={() => openProject("")}
      onView={onView}
    />
  ) : (
    <ProjectsIndex {...shared} projects={projects} tab={section} error={error} onTab={onSection} onOpen={openProject} />
  );
}
