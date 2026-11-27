import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, ConnectableService, Connection, ConnectionsReport, DriveFolder, GoogleService, Project } from "./api";
import ProjectSelect, { useProjects } from "./ProjectSelect";

type Props = { onAuthError: (err: unknown) => void };
type Run = (fn: () => Promise<string>) => Promise<void>;

const NAMES: Record<GoogleService, string> = { calendar: "Google Calendar", drive: "Google Drive", gmail: "Gmail" };
const WHAT: Record<GoogleService, string> = {
  calendar: "Events from 30 days back to 6 months ahead are copied into pal, so it can answer \"am I free?\" and list them on Schedule.",
  drive: "Files in the folders you pick are copied into pal's documents, so chats can search and cite them.",
  gmail: "Searched live when you ask a chat about mail. Nothing from Gmail is stored in pal.",
};

function ago(iso: string | null): string {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function syncMessage(r: Record<string, string | number>): string {
  if (r.status === "busy") return "A sync is already running.";
  if (r.status === "skipped") return "Paused connections don't sync.";
  if (r.status === "stopped") return "Sync stopped before finishing; the next one picks up from there.";
  if (r.status === "failed") return "Sync failed; see the error on the card.";
  const parts = (["added", "updated", "removed", "unchanged", "skipped"] as const)
    .filter((k) => Number(r[k] ?? 0) > 0)
    .map((k) => `${r[k]} ${k}`);
  return parts.length ? `Synced: ${parts.join(", ")}.` : "Synced; nothing changed.";
}

function Command({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () =>
    navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      () => undefined,
    );
  return (
    <span className="conn-cmd">
      <code>{text}</code>
      <button type="button" className="small" onClick={copy}>
        {copied ? "Copied" : "Copy"}
      </button>
    </span>
  );
}

export default function Connections({ onAuthError }: Props) {
  const [report, setReport] = useState<ConnectionsReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const projects = useProjects();

  const load = useCallback(
    () =>
      api
        .connections()
        .then((r) => {
          setReport(r);
          setLoadError("");
          if (r.signin) {
            setNotice(r.signin.ok ? r.signin.message : "");
            setError(r.signin.ok ? "" : r.signin.message);
          }
        })
        .catch((err) => {
          onAuthError(err);
          setLoadError(err instanceof Error ? err.message : "Could not load connections.");
        }),
    [onAuthError],
  );
  useEffect(() => {
    load();
    const t = setInterval(load, 30_000); // the background sync updates "last synced" and errors
    return () => clearInterval(t);
  }, [load]);

  const run: Run = async (fn) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      setNotice(await fn());
    } catch (err) {
      onAuthError(err);
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
      load();
    }
  };

  if (!report)
    return (
      <section className="memory">
        <h2>Connections</h2>
        {loadError ? <p className="error small">{loadError}</p> : <p className="muted">Loading…</p>}
      </section>
    );
  const byService = new Map(report.connections.map((c) => [c.service, c]));

  return (
    <section className="memory connections">
      <header className="memory-head">
        <h2>Connections</h2>
        <button className="small" onClick={load} disabled={busy}>
          Refresh
        </button>
      </header>
      <p className="muted small">
        Read-only access to your own Google account. What pal copies stays on this PC, and text from these services is
        treated as information, never as instructions. Signing in works only in a browser on this PC, so no phone or
        other device can start a connection.
      </p>
      {!report.google_client && (
        <div className="memory-item conn-setup">
          <p className="memory-text">
            <strong>First, store your Google OAuth client.</strong> Create a Desktop client in your own Google Cloud
            project (the README's "Connect Google" section walks through it), download its JSON, then run:
          </p>
          <Command text={report.client_command} />
        </div>
      )}
      {notice && <p className="notice small">{notice}</p>}
      {error && <p className="error small pre">{error}</p>}
      <ul className="memory-list">
        {report.services.map((s) => {
          const c = byService.get(s.service);
          return c ? (
            <Connected key={s.service} c={c} busy={busy} run={run} projects={projects} />
          ) : (
            <NotConnected key={s.service} s={s} ready={report.google_client} busy={busy} run={run} />
          );
        })}
      </ul>
    </section>
  );
}

// Off to Google in this tab; it comes back to /?view=connections, where the result shows once.
async function signIn(service: GoogleService): Promise<string> {
  const { url } = await api.googleStart(service);
  window.location.assign(url);
  return "Opening Google sign-in…";
}

type NotConnectedProps = { s: ConnectableService; ready: boolean; busy: boolean; run: Run };

function NotConnected({ s, ready, busy, run }: NotConnectedProps) {
  return (
    <li className="memory-item">
      <p className="memory-text">
        <strong>{NAMES[s.service]}</strong> <span className="badge">not connected</span>
      </p>
      <p className="small muted">{WHAT[s.service]}</p>
      <p className="small muted">pal will ask Google to {s.grants}.</p>
      <div className="memory-meta small">
        <button className="small primary" disabled={busy || !ready} onClick={() => run(() => signIn(s.service))}>
          Sign in with Google
        </button>
        {!ready && <span className="muted">Store the OAuth client first.</span>}
      </div>
      {ready && (
        <details className="small muted">
          <summary>Or connect from a terminal</summary>
          <Command text={s.command} />
        </details>
      )}
    </li>
  );
}

type ConnectedProps = { c: Connection; busy: boolean; run: Run; projects: Project[] };

function Connected({ c, busy, run, projects }: ConnectedProps) {
  const [confirm, setConfirm] = useState(false);
  const state = !c.enabled ? "paused" : c.last_error ? "error" : c.last_ok_at || c.service === "gmail" ? "on" : "waiting";
  const label = { paused: "paused", error: "last sync failed", on: "connected", waiting: "first sync pending" }[state];
  return (
    <li className={`memory-item conn conn-${state}`}>
      <p className="memory-text">
        <strong>{NAMES[c.service]}</strong> <span className={`badge conn-state conn-${state}`}>{label}</span>
      </p>
      <div className="memory-meta small muted">
        <span>{c.account ?? "your Google account"}</span>
        {c.service === "gmail" ? (
          <span>searched live · nothing stored</span>
        ) : (
          <>
            <span>
              {c.items} {c.service === "calendar" ? "events" : "files"}
            </span>
            <span title={c.last_sync_at ?? undefined}>synced {ago(c.last_ok_at)}</span>
          </>
        )}
      </div>
      {c.last_error && (
        <div className="memory-meta small">
          <p className="error small conn-error-text">{c.last_error}</p>
          <button className="small" disabled={busy} onClick={() => run(() => signIn(c.service))} title="Fixes an expired or revoked sign-in">
            Sign in again
          </button>
        </div>
      )}
      {c.service === "drive" && <DriveFolders c={c} busy={busy} run={run} />}
      <div className="memory-meta small">
        {c.service === "drive" && (
          <>
            <span className="muted">Project for synced files:</span>
            <ProjectSelect
              value={c.project_id}
              projects={projects}
              disabled={busy}
              onChange={(id) => run(async () => (await api.patchConnection(c.id, { project_id: id }), "Updated."))}
            />
          </>
        )}
        <span className="spacer" />
        {c.service !== "gmail" && (
          <button className="small" disabled={busy || !c.enabled} onClick={() => run(async () => syncMessage(await api.syncConnection(c.id)))}>
            Sync now
          </button>
        )}
        <button
          className="small"
          disabled={busy}
          title={c.enabled ? "Stop syncing (or searching) without removing anything" : undefined}
          onClick={() => run(async () => (await api.patchConnection(c.id, { enabled: !c.enabled }), c.enabled ? "Paused." : "Resumed."))}
        >
          {c.enabled ? "Pause" : "Resume"}
        </button>
        {confirm ? (
          <>
            <button
              className="danger small"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const r = await api.disconnect(c.id);
                  const n = c.service === "drive" ? r.documents_removed : c.items; // calendar events go with the row
                  const removed = c.service === "gmail" ? "" : ` Removed ${n} synced item${n === 1 ? "" : "s"}.`;
                  return `Disconnected ${NAMES[c.service]}.${removed}${r.note ? " " + r.note : ""}`;
                })
              }
            >
              Disconnect and delete
            </button>
            <button className="small" onClick={() => setConfirm(false)}>
              Keep
            </button>
          </>
        ) : (
          <button
            className="small"
            disabled={busy}
            onClick={() => setConfirm(true)}
            title="Removes the connection and everything pal copied from it"
          >
            Disconnect
          </button>
        )}
      </div>
    </li>
  );
}

function DriveFolders({ c, busy, run }: { c: Connection; busy: boolean; run: Run }) {
  const [link, setLink] = useState("");
  const folders: DriveFolder[] = c.config.folders ?? [];
  const add = (e: FormEvent) => {
    e.preventDefault();
    const text = link.trim();
    if (!text) return;
    run(async () => {
      const r = await api.addDriveFolder(c.id, text);
      setLink("");
      return `Added ${r.added.name}. pal syncs it and the folders inside it at the next sync.`;
    });
  };
  return (
    <div className="conn-folders">
      {folders.length === 0 ? (
        <p className="small notice-inline">No folders picked yet, so nothing syncs. Add a folder link from Drive:</p>
      ) : (
        <ul className="conn-folder-list small">
          {folders.map((f) => (
            <li key={f.id}>
              <span aria-hidden>📁</span> {f.name}
              <button
                className="small"
                disabled={busy}
                title="Stop syncing this folder; its files leave pal at the next sync"
                onClick={() => run(async () => (await api.removeDriveFolder(c.id, f.id), `Removed ${f.name}. Its files leave pal at the next sync.`))}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <form className="memory-add" onSubmit={add}>
        <input
          placeholder="https://drive.google.com/drive/folders/…"
          aria-label="Drive folder link"
          value={link}
          onChange={(e) => setLink(e.target.value)}
          disabled={busy}
        />
        <button disabled={busy || !link.trim()}>Add folder</button>
      </form>
    </div>
  );
}
