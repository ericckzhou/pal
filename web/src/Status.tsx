import { useEffect, useState } from "react";
import { api, Encryption, Status as StatusData } from "./api";
import Attention from "./Attention";
import { bytes, duration, num, relTime } from "./format";
import { Icon } from "./Icon";
import { View } from "./Sidebar";

type Props = {
  onOpenConversation: (id: string) => void;
  onView: (v: View) => void;
  onStatus: (s: StatusData) => void;
  onAuthError: (err: unknown) => void;
  owner: boolean;
};

const ENCRYPTION_LABEL: Record<Encryption["state"], string> = {
  on: "Encrypted",
  encrypting: "Encrypting…",
  decrypting: "Decrypting",
  off: "Not encrypted",
  suspended: "Suspended",
  waiting: "Not turned on",
  unavailable: "Not encrypted",
  unknown: "Unknown",
};

function Tile({ label, value, sub, state, onClick }: {
  label: string; value: string; sub?: string; state?: "ok" | "bad" | "warn"; onClick?: () => void;
}) {
  const body = (
    <>
      <span className="tile-label">
        {state && <span className={`dot ${state}`} aria-hidden="true" />}
        {label}
      </span>
      <span className="tile-value">{value}</span>
      {sub && <span className="tile-sub">{sub}</span>}
    </>
  );
  return onClick ? (
    <button className="tile" onClick={onClick}>{body}</button>
  ) : (
    <div className="tile">{body}</div>
  );
}

// Live overview of this pal install. Refreshes every 5 seconds while open.
export default function Status({ onOpenConversation, onView, onStatus, onAuthError, owner }: Props) {
  const [data, setData] = useState<StatusData | null>(null);
  const [error, setError] = useState("");
  const [acked, setAcked] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .status()
        .then((s) => {
          if (!alive) return;
          setData(s);
          setError("");
          onStatus(s);
        })
        .catch((err) => {
          onAuthError(err);
          if (alive) setError("Can't reach pal's server.");
        });
    load();
    const t = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [onAuthError, onStatus]);

  if (!data) return <section className="page">{error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}</section>;
  const c = data.counts;
  const backupAge = data.backups.latest_at ? (Date.now() - new Date(data.backups.latest_at).getTime()) / 3.6e6 : null;

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h2>Status</h2>
          <p className="muted small">
            pal v{data.version} · up {duration(data.uptime_s)} · refreshes every 5 s
          </p>
        </div>
      </header>
      {error && <p className="error">{error}</p>}

      <div className="tiles">
        <Tile
          label="Model"
          value={data.model}
          sub={data.ollama.up ? `Ollama ready · ${num(data.num_ctx)} context` : "Ollama not responding"}
          state={data.ollama.up ? "ok" : "bad"}
          onClick={() => onView("settings")}
        />
        <Tile label="Database" value="Connected" sub={`${num(c.messages)} messages stored`} state="ok" />
        <Tile
          label="Backups"
          value={data.backups.latest_at ? relTime(data.backups.latest_at) : "None yet"}
          sub={
            data.backups.plaintext > 0
              ? `${data.backups.plaintext} not yet encrypted · next backup fixes this`
              : `${data.backups.count} kept, encrypted · ${bytes(data.backups.latest_bytes)} latest`
          }
          state={backupAge === null || backupAge > 36 || data.backups.plaintext > 0 ? "warn" : "ok"}
        />
        <Tile label="Disk free" value={bytes(data.disk_free)} sub="where backups are saved" />
        <Tile label="Active chats" value={String(c.active_chats)} sub="in the last 5 minutes" onClick={() => onView("sessions")} />
        <Tile label="Tokens, 24 h" value={num(c.tokens_24h)} sub="read + written" onClick={() => onView("analytics")} />
        <Tile
          label="Memory"
          value={num(c.memories)}
          sub={c.inbox ? `${c.inbox} suggestions waiting` : "inbox empty"}
          state={c.inbox ? "warn" : undefined}
          onClick={() => onView("memory")}
        />
        <Tile
          label="Documents"
          value={num(c.documents)}
          sub={c.embed_backlog ? `${num(c.embed_backlog)} passages still indexing` : "all indexed"}
          onClick={() => onView("documents")}
        />
        <Tile label="Open tasks" value={num(c.open_tasks)} onClick={() => onView("tasks")} />
        <Tile
          label="Reminders"
          value={String(c.reminders)}
          sub={c.next_reminder ? `next ${relTime(c.next_reminder)}` : "none scheduled"}
          onClick={() => onView("schedule")}
        />
        <Tile label="Paired devices" value={String(c.devices)} onClick={() => onView("devices")} />
        {data.encryption && (
          <Tile
            label={`Disk encryption (${data.encryption.drive})`}
            value={ENCRYPTION_LABEL[data.encryption.state]}
            sub="checked once at startup"
            state={["on", "encrypting"].includes(data.encryption.state) ? "ok" : data.encryption.state === "unknown" ? undefined : "warn"}
          />
        )}
      </div>

      {owner && data.encryption && !data.encryption.acknowledged && !acked &&
        data.pressure.some((p) => p.kind === "encryption") && (
        <div className="panel encryption-note">
          <span className="small">
            <Icon name="lock" size={14} /> {data.encryption.drive} isn't protected by disk encryption. pal keeps working;
            this is shown once.
          </span>
          <button
            className="small"
            onClick={() =>
              api.ackEncryption().then(() => setAcked(true)).catch(onAuthError)
            }
          >
            Got it, don't show again
          </button>
        </div>
      )}

      <Attention onView={onView} onAuthError={onAuthError} />

      <h3 className="section">Recent sessions</h3>
      {data.recent.length === 0 ? (
        <p className="muted">No chats yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col" aria-label="Open" />
                <th scope="col">Chat</th>
                <th scope="col">Model</th>
                <th scope="col" className="num">Messages</th>
                <th scope="col" className="num">Tokens</th>
                <th scope="col">Last active</th>
              </tr>
            </thead>
            <tbody>
              {data.recent.map((r) => (
                <tr key={r.id}>
                  <td>
                    <button className="icon-btn" onClick={() => onOpenConversation(r.id)} aria-label={`Open ${r.title}`} title="Open">
                      <Icon name="play" size={14} />
                    </button>
                  </td>
                  <td className="cell-title">
                    <strong>{r.title}</strong>
                    {r.preview && r.preview !== r.title && <span className="muted small">{r.preview}</span>}
                  </td>
                  <td className="mono small">{r.chosen_model ?? r.last_model ?? "—"}</td>
                  <td className="num">{r.message_count}</td>
                  <td className="num">{num(r.tokens)}</td>
                  <td className="small">{relTime(r.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
