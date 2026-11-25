import { useEffect, useState } from "react";
import { api, Project } from "./api";

/** Active projects, loaded once per view. Failures leave the list empty (the picker then shows only "No project"). */
export function useProjects(): Project[] {
  const [projects, setProjects] = useState<Project[]>([]);
  useEffect(() => {
    api.projects().then(setProjects).catch(() => setProjects([]));
  }, []);
  return projects;
}

type Props = {
  value: string | null;
  projects: Project[];
  onChange: (id: string | null) => void;
  mixed?: boolean;
  disabled?: boolean;
};

/** Optional project tag. Items in a chat's focus project rank higher there; untagged items stay available everywhere. */
export default function ProjectSelect({ value, projects, onChange, mixed = false, disabled = false }: Props) {
  const known = value === null || projects.some((p) => p.id === value);
  return (
    <select
      aria-label="Project"
      title="Items in a project rank higher in chats focused on that project"
      value={mixed ? "__mixed" : value ?? ""}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value || null)}
    >
      {mixed && (
        <option value="__mixed" disabled>
          mixed projects
        </option>
      )}
      <option value="">no project</option>
      {!known && <option value={value!}>archived project</option>}
      {projects.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}
        </option>
      ))}
    </select>
  );
}
