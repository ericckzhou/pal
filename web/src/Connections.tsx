import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  api,
  ConnectableService,
  Connection,
  ConnectionsReport,
  DriveFolder,
  GoogleService,
  Project,
  StarcallCard,
  StarcallRuntime,
  StarcallScope,
} from "./api";
import ProjectSelect, { useProjects } from "./ProjectSelect";

type Props = { onAuthError: (err: unknown) => void };
// `here` shows the outcome inside the card that asked, instead of at the top of the page.
type Here = { notice: (m: string) => void; error: (m: string) => void };
type Run = (fn: () => Promise<string>, here?: Here) => Promise<void>;

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

  const run: Run = async (fn, here) => {
    setBusy(true);
    setError("");
    setNotice("");
    here?.notice("");
    here?.error("");
    try {
      (here?.notice ?? setNotice)(await fn());
    } catch (err) {
      onAuthError(err);
      (here?.error ?? setError)(err instanceof Error ? err.message : "Something went wrong.");
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
      <h3 className="conn-group">On this PC</h3>
      <ul className="memory-list">
        <Starcall card={report.starcall} busy={busy} run={run} />
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

const PATH_HINT = "C:\\…\\StarcallOS\\packages\\mcp-server\\dist\\index.js";

function runtimeLine(r: StarcallRuntime | null): string | null {
  if (!r) return null;
  if (r.state === "running") return "server running";
  if (r.state === "cooldown") return `waiting ${Math.ceil(r.retry_in ?? 0)} s before trying again`;
  if (r.state === "disabled") return "off until you press Test";
  return null;
}

function testMessage(r: { ok: boolean; status: StarcallRuntime }): string {
  if (r.ok) return "StarcallOS answered. Chats with Search StarcallOS on can use it now.";
  throw new Error(r.status.error?.message ?? "StarcallOS didn't start. See the card for details.");
}

/** StarcallOS: a server path pal runs on this PC, not a sign-in. Its library is searched live and never stored. */
function Starcall({ card, busy, run }: { card: StarcallCard; busy: boolean; run: Run }) {
  const c = card.connection;
  const savedPath = c?.server_path ?? "";
  const savedScopes = (c?.scopes ?? ["passages"]).join(",");
  const [path, setPath] = useState(savedPath);
  const [scopes, setScopes] = useState<StarcallScope[]>(savedScopes.split(",") as StarcallScope[]);
  const [testing, setTesting] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const here: Here = { notice: setNotice, error: setError };
  const act = (fn: () => Promise<string>) => run(fn, here);
  useEffect(() => {
    setPath(savedPath);
    setScopes(savedScopes.split(",") as StarcallScope[]);
  }, [savedPath, savedScopes]);

  const dirty = path.trim() !== savedPath || scopes.join(",") !== savedScopes;
  const state = !c ? "none" : !c.enabled ? "paused" : c.last_error ? "error" : c.last_ok_at ? "on" : "waiting";
  const label = { none: "not set up", paused: "paused", error: "not working", on: "connected", waiting: "not tested yet" }[state];
  const toggle = (name: StarcallScope, on: boolean) =>
    setScopes((cur) => card.scopes.map((s) => s.name).filter((n) => n === "passages" || (n === name ? on : cur.includes(n))));
  const save = (e: FormEvent) => {
    e.preventDefault();
    act(async () => {
      const card = await api.saveStarcall(path, scopes);
      const saved = card.connection?.server_path ?? path;
      const filled = saved !== path.trim().replace(/^"|"$/g, "") ? ` (completed to ${saved})` : "";
      return `Saved${filled}. Press Test to check that pal can start it.`;
    });
  };
  const test = () => {
    setTesting(true);
    act(async () => testMessage(await api.testStarcall())).finally(() => setTesting(false));
  };
  const extra = runtimeLine(card.runtime);

  return (
    <li className={`memory-item conn conn-${state === "none" ? "waiting" : state}`}>
      <p className="memory-text">
        <strong>StarcallOS</strong> <span className={`badge conn-state conn-${state}`}>{label}</span>
      </p>
      <p className="small muted">
        Your StarcallOS library, searched live when a chat has <em>Search StarcallOS</em> on (off by default). pal starts
        StarcallOS's read-only server on this PC, uses at most 2 of its items per reply, and keeps none of their text.
      </p>
      {!card.native ? (
        <p className="small notice-inline">{card.reason}.</p>
      ) : (
        <>
          <form className="conn-starcall" onSubmit={save}>
            <label className="small conn-path">
              Server path
              <input
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder={PATH_HINT}
                title="Your StarcallOS folder, or packages\mcp-server\dist\index.js inside it"
                aria-label="StarcallOS server path"
                spellCheck={false}
                disabled={busy}
              />
            </label>
            <fieldset className="conn-scopes small" disabled={busy}>
              <legend>What pal may search</legend>
              {card.scopes.map((s) => (
                <label key={s.name}>
                  <input
                    type="checkbox"
                    checked={s.name === "passages" || scopes.includes(s.name)}
                    disabled={s.name === "passages"}
                    onChange={(e) => toggle(s.name, e.target.checked)}
                  />
                  {s.label} <span className="muted">— {s.what}</span>
                </label>
              ))}
            </fieldset>
            <div className="memory-meta small">
              <button className={c ? "small" : "small primary"} disabled={busy || !path.trim() || !dirty}>
                {c ? "Save changes" : "Save"}
              </button>
              {!c && (
                <span className="muted">
                  Build it first: <code>pnpm -C packages/mcp-server build</code> in your StarcallOS checkout (needs Node 22.13+).
                </span>
              )}
            </div>
          </form>
          {notice && <p className="notice small" role="status">{notice}</p>}
          {error && <p className="error small pre" role="alert">{error}</p>}
          {c && (
            <>
              <div className="memory-meta small muted">
                {c.last_ok_at && <span title={c.last_ok_at}>answered {ago(c.last_ok_at)}</span>}
                <span>{card.meaning_search ? "keyword + meaning search (pal's Ollama)" : "keyword search only"}</span>
                {extra && <span>{extra}</span>}
              </div>
              {c.last_error && <p className="error small conn-error-text">{c.last_error}</p>}
              <div className="memory-meta small">
                <button className="small primary" disabled={busy || dirty} onClick={test} title="Start StarcallOS's server and check that it answers">
                  {testing ? "Testing…" : "Test"}
                </button>
                {dirty && <span className="muted">Save first</span>}
                <span className="spacer" />
                <button
                  className="small"
                  disabled={busy}
                  title={c.enabled ? "Chats stop searching StarcallOS and its server stops; nothing is removed" : undefined}
                  onClick={() => act(async () => (await api.pauseStarcall(!c.enabled), c.enabled ? "Paused." : "Resumed."))}
                >
                  {c.enabled ? "Pause" : "Resume"}
                </button>
                {confirm ? (
                  <>
                    <button
                      className="danger small"
                      disabled={busy}
                      onClick={() => act(async () => (await api.removeStarcall(), setConfirm(false), "Removed StarcallOS. Nothing in StarcallOS changed."))}
                    >
                      Remove
                    </button>
                    <button className="small" onClick={() => setConfirm(false)}>
                      Keep
                    </button>
                  </>
                ) : (
                  <button className="small" disabled={busy} onClick={() => setConfirm(true)} title="Forget the server path; pal copied nothing from StarcallOS">
                    Remove
                  </button>
                )}
              </div>
            </>
          )}
          <p className="small muted">
            StarcallOS runs as you, with no sandbox, so only point this at your own StarcallOS checkout. pal doesn't hand
            it pal's database login or settings.
          </p>
        </>
      )}
    </li>
  );
}
