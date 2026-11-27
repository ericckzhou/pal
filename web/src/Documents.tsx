import { ChangeEvent, FormEvent, useCallback, useEffect, useState } from "react";
import { api, Doc, Project } from "./api";
import DocProcessing from "./DocProcessing";
import ProjectSelect, { useProjects } from "./ProjectSelect";

type Props = { embedded?: boolean; projectId?: string | null; onAuthError: (err: unknown) => void };
type Run = (label: string, fn: () => Promise<string>) => Promise<void>;

const kb = (n: number) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);
const progress = (parts: number, pending: number) => (pending > 0 ? `indexing ${parts - pending}/${parts}` : `${parts} parts`);

function folderName(path: string): string {
  return path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path;
}

const DRIVE = "google-drive:";
const fromDrive = (d: Doc) => d.source_path?.startsWith(DRIVE) ?? false;

/** The file's path inside its folder, e.g. probes\probe-07\README.md */
function relativePath(d: Doc): string {
  if (d.folder && d.source_path?.startsWith(d.folder)) return d.source_path.slice(d.folder.length).replace(/^[\\/]+/, "");
  return d.filename;
}

export default function Documents({ embedded = false, projectId = null, onAuthError }: Props) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [path, setPath] = useState("");
  const projects = useProjects();
  const [target, setTarget] = useState<string | null>(projectId); // project for files added from here
  useEffect(() => setTarget(projectId), [projectId]);

  const load = useCallback(() => {
    api.documents().then(setDocs).catch(onAuthError);
  }, [onAuthError]);

  useEffect(() => {
    load();
  }, [load]);

  // While anything is still being indexed for meaning-based search, refresh every few seconds.
  const indexing = docs.some((d) => d.pending > 0);
  useEffect(() => {
    if (!indexing) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [indexing, load]);

  const run: Run = async (label, fn) => {
    setBusy(true);
    setError("");
    setStatus(label);
    try {
      setStatus(await fn());
    } catch (err) {
      onAuthError(err);
      setStatus("");
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
      load();
    }
  };

  const onFiles = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (!files.length) return;
    run(`Adding ${files.length} file${files.length > 1 ? "s" : ""}…`, async () => {
      const failed: string[] = [];
      let added = 0;
      for (const f of files) {
        try {
          await api.uploadDocument(f, target);
          added++;
        } catch (err) {
          onAuthError(err);
          failed.push(`${f.name}: ${err instanceof Error ? err.message : "failed"}`);
        }
      }
      if (failed.length) setError(failed.join("\n"));
      return `Added ${added} of ${files.length}.`;
    });
  };

  const addFolderNow = async (folder: string) => {
    const r = await api.addFolder(folder, target);
    if (r.skipped.length) setError(r.skipped.map((s) => `${s.path}: ${s.reason}`).join("\n"));
    return `Added ${r.added}, updated ${r.updated}, unchanged ${r.unchanged}${r.skipped.length ? `, skipped ${r.skipped.length}` : ""}.`;
  };

  const scan = (folder: string) => run("Reading folder…", () => addFolderNow(folder));

  const browse = () =>
    run("Choose a folder in the window that just opened…", async () => {
      const { path: picked } = await api.pickFolder();
      if (!picked) return "No folder chosen.";
      setPath(picked);
      setStatus("Reading folder…");
      return addFolderNow(picked);
    });

  const onFolder = (e: FormEvent) => {
    e.preventDefault();
    if (path.trim()) scan(path.trim());
  };

  const folders = new Map<string, Doc[]>();
  const loose: Doc[] = [];
  const drive: Doc[] = [];
  for (const d of projectId ? docs.filter((x) => x.project_id === projectId) : docs) {
    if (fromDrive(d)) drive.push(d);
    else if (d.folder) folders.set(d.folder, [...(folders.get(d.folder) ?? []), d]);
    else loose.push(d);
  }
  const folderList = [...folders.entries()].sort(([a], [b]) => folderName(a).localeCompare(folderName(b)));

  return (
    <section className={embedded ? "embedded docs" : "memory docs"}>
      <header className="memory-head">
        {embedded ? <span /> : <h2>Documents</h2>}
        <label className={`button primary ${busy ? "disabled" : ""}`}>
          Add files
          <input type="file" multiple accept=".pdf,.md,.markdown,.txt,.text,.rst" onChange={onFiles} disabled={busy} hidden />
        </label>
      </header>
      <div className="memory-meta small">
        <span className="muted">Project for files you add:</span>
        <ProjectSelect value={target} projects={projects} onChange={setTarget} disabled={busy} />
      </div>
      <p className="muted small">
        PDF, Markdown and text. pal searches them when you chat and cites what it used. Their text is treated as
        information, never as instructions.
      </p>
      <form className="memory-add" onSubmit={onFolder}>
        <input
          placeholder="Or a folder on this PC, e.g. C:\Users\you\Documents\notes"
          value={path}
          onChange={(e) => setPath(e.target.value)}
        />
        <button type="button" onClick={browse} disabled={busy} title="Pick a folder in Windows Explorer">
          Browse…
        </button>
        <button disabled={busy || !path.trim()}>Add folder</button>
      </form>
      {status && <p className="notice small">{status}</p>}
      {error && <p className="error small pre">{error}</p>}
      {!embedded && <DocProcessing onProgress={load} onAuthError={onAuthError} />}

      <ul className="memory-list">
        {folderList.length + loose.length + drive.length === 0 && <li className="muted">{projectId ? "No documents in this project." : "No documents yet."}</li>}
        {folderList.map(([folder, items]) => (
          <FolderItem key={folder} folder={folder} items={items} busy={busy} run={run} projects={projects} onRescan={() => scan(folder)} />
        ))}
        {drive.length > 0 && <DriveGroup items={drive} run={run} projects={projects} />}
        {loose.length > 0 && folderList.length + drive.length > 0 && <li className="small muted section">Uploaded and single files</li>}
        {loose.map((d) => (
          <DocItem key={d.id} d={d} label={d.source_path ? d.filename : "uploaded"} run={run} projects={projects} />
        ))}
      </ul>
    </section>
  );
}

type FolderProps = { folder: string; items: Doc[]; busy: boolean; run: Run; projects: Project[]; onRescan: () => void };

function FolderItem({ folder, items, busy, run, projects, onRescan }: FolderProps) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const parts = items.reduce((n, d) => n + d.chunk_count, 0);
  const pending = items.reduce((n, d) => n + d.pending, 0);
  const size = items.reduce((n, d) => n + d.size_bytes, 0);
  const tags = new Set(items.map((d) => d.project_id));
  const tag = tags.size === 1 ? items[0].project_id : null;
  const sorted = [...items].sort((a, b) => relativePath(a).localeCompare(relativePath(b)));
  return (
    <li className="memory-item folder">
      <button className="link folder-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span aria-hidden>{open ? "▾" : "▸"}</span> <strong>📁 {folderName(folder)}</strong>
      </button>
      <div className="memory-meta small muted">
        <span title={folder} className="folder-path">{folder}</span>
        <span>
          {items.length} file{items.length === 1 ? "" : "s"}
        </span>
        <span>{kb(size)}</span>
        <span>{progress(parts, pending)}</span>
        <span className="spacer" />
        <ProjectSelect
          value={tag}
          mixed={tags.size > 1}
          projects={projects}
          disabled={busy}
          onChange={(id) => run("Updating…", async () => `Moved ${(await api.setFolderProject(folder, id)).updated} files.`)}
        />
        <button className="small" disabled={busy} onClick={onRescan} title="Pick up new and changed files">
          Rescan
        </button>
        {confirm ? (
          <>
            <button
              className="danger small"
              onClick={() => run("Removing…", async () => `Removed ${(await api.removeFolder(folder)).removed} files from pal.`)}
            >
              Remove from pal
            </button>
            <button className="small" onClick={() => setConfirm(false)}>
              Keep
            </button>
          </>
        ) : (
          <button className="small" onClick={() => setConfirm(true)} title="Removes these documents from pal; the files on your PC stay">
            Remove
          </button>
        )}
      </div>
      {open && (
        <ul className="memory-list folder-files">
          {sorted.map((d) => (
            <DocItem key={d.id} d={d} label={relativePath(d)} run={run} projects={projects} compact />
          ))}
        </ul>
      )}
    </li>
  );
}

function DriveGroup({ items, run, projects }: { items: Doc[]; run: Run; projects: Project[] }) {
  const [open, setOpen] = useState(false);
  const parts = items.reduce((n, d) => n + d.chunk_count, 0);
  const pending = items.reduce((n, d) => n + d.pending, 0);
  const size = items.reduce((n, d) => n + d.size_bytes, 0);
  const sorted = [...items].sort((a, b) => a.title.localeCompare(b.title));
  return (
    <li className="memory-item folder drive-group">
      <button className="link folder-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span aria-hidden>{open ? "▾" : "▸"}</span> <strong>☁ Google Drive</strong>
      </button>
      <div className="memory-meta small muted">
        <span>
          {items.length} file{items.length === 1 ? "" : "s"}
        </span>
        <span>{kb(size)}</span>
        <span>{progress(parts, pending)}</span>
        <span className="spacer" />
        <span>Synced read-only. Pick folders, or disconnect, on the Connections page.</span>
      </div>
      {open && (
        <ul className="memory-list folder-files">
          {sorted.map((d) => (
            <DocItem key={d.id} d={d} label={d.filename} run={run} projects={projects} compact synced />
          ))}
        </ul>
      )}
    </li>
  );
}

type DocProps = { d: Doc; label: string; run: Run; projects: Project[]; compact?: boolean; synced?: boolean };

function DocItem({ d, label, run, projects, compact = false, synced = false }: DocProps) {
  const [confirm, setConfirm] = useState(false);
  return (
    <li className={compact ? "doc-row" : "memory-item"}>
      <p className="memory-text">
        <strong>{d.title}</strong>
      </p>
      <div className="memory-meta small muted">
        <span className="badge">{d.kind}</span>
        <span title={d.source_path ?? "uploaded"}>{label}</span>
        <span>{kb(d.size_bytes)}</span>
        <span>{progress(d.chunk_count, d.pending)}</span>
        <span className="spacer" />
        <ProjectSelect
          value={d.project_id}
          projects={projects}
          onChange={(id) => run("Updating…", async () => (await api.setDocumentProject(d.id, id), "Updated."))}
        />
        {synced ? null : confirm ? (
          <>
            <button className="danger small" onClick={() => run("Deleting…", async () => (await api.deleteDocument(d.id), "Deleted."))}>
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
