import { useEffect, useState } from "react";
import { api, Message, SessionRow } from "./api";
import { dateTime, num, relTime } from "./format";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";

type Props = {
  version: number;
  onOpenConversation: (id: string) => void;
  onChanged: () => void;
  onAuthError: (err: unknown) => void;
};

// Every chat with its stats: search, read, rename, export, delete (one or many).
export default function Sessions({ version, onOpenConversation, onChanged, onAuthError }: Props) {
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);
  const [confirmBulk, setConfirmBulk] = useState(false);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => api.sessions(query).then(setRows).catch(onAuthError), query ? 250 : 0);
    return () => clearTimeout(t);
  }, [query, version, tick, onAuthError]);

  const toggle = (id: string) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const expand = async (id: string) => {
    if (expanded === id) {
      setExpanded(null);
      return;
    }
    try {
      setMessages((await api.get(id)).messages);
      setExpanded(id);
    } catch (err) {
      onAuthError(err);
    }
  };

  const rename = async () => {
    if (!renaming || !renaming.title.trim()) return;
    try {
      await api.patchConversation(renaming.id, { title: renaming.title });
      setRenaming(null);
      setTick((t) => t + 1);
      onChanged();
    } catch (err) {
      onAuthError(err);
      setError(err instanceof Error ? err.message : "Could not rename.");
    }
  };

  const removeSelected = async () => {
    try {
      await api.bulkDelete([...selected]);
      setSelected(new Set());
      setConfirmBulk(false);
      setTick((t) => t + 1);
      onChanged();
    } catch (err) {
      onAuthError(err);
      setError(err instanceof Error ? err.message : "Could not delete.");
    }
  };

  const allChecked = rows.length > 0 && rows.every((r) => selected.has(r.id));

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h2>Sessions</h2>
          <p className="muted small">{rows.length} chats{query ? " match" : ""}</p>
        </div>
        <input className="search page-search" placeholder="Search titles and messages" value={query} onChange={(e) => setQuery(e.target.value)} />
      </header>
      {error && <p className="error">{error}</p>}

      {selected.size > 0 && (
        <div className="bulk-bar">
          <span>{selected.size} selected</span>
          {confirmBulk ? (
            <>
              <span className="muted small">Delete these chats and their messages for good?</span>
              <button className="danger small" onClick={removeSelected}>Delete {selected.size}</button>
              <button className="small" onClick={() => setConfirmBulk(false)}>Keep</button>
            </>
          ) : (
            <>
              <button className="danger small" onClick={() => setConfirmBulk(true)}>
                <Icon name="trash" size={14} /> Delete
              </button>
              <button className="small" onClick={() => setSelected(new Set())}>Clear</button>
            </>
          )}
        </div>
      )}

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">
                <input
                  type="checkbox"
                  checked={allChecked}
                  onChange={() => setSelected(allChecked ? new Set() : new Set(rows.map((r) => r.id)))}
                  aria-label="Select all"
                />
              </th>
              <th scope="col">Chat</th>
              <th scope="col">Model</th>
              <th scope="col" className="num">Msgs</th>
              <th scope="col" className="num">Tokens</th>
              <th scope="col">Last active</th>
              <th scope="col" aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <SessionRowView
                key={r.id}
                r={r}
                checked={selected.has(r.id)}
                expanded={expanded === r.id}
                messages={expanded === r.id ? messages : []}
                renaming={renaming?.id === r.id ? renaming.title : null}
                onToggle={() => toggle(r.id)}
                onExpand={() => expand(r.id)}
                onOpen={() => onOpenConversation(r.id)}
                onRenameStart={() => setRenaming({ id: r.id, title: r.title })}
                onRenameChange={(title) => setRenaming({ id: r.id, title })}
                onRenameSave={rename}
                onRenameCancel={() => setRenaming(null)}
              />
            ))}
          </tbody>
        </table>
      </div>
      {rows.length === 0 && <p className="muted">{query ? "No chats match." : "No chats yet."}</p>}
    </section>
  );
}

type RowProps = {
  r: SessionRow;
  checked: boolean;
  expanded: boolean;
  messages: Message[];
  renaming: string | null;
  onToggle: () => void;
  onExpand: () => void;
  onOpen: () => void;
  onRenameStart: () => void;
  onRenameChange: (t: string) => void;
  onRenameSave: () => void;
  onRenameCancel: () => void;
};

function SessionRowView(p: RowProps) {
  const { r } = p;
  return (
    <>
      <tr className={p.expanded ? "expanded" : ""}>
        <td>
          <input type="checkbox" checked={p.checked} onChange={p.onToggle} aria-label={`Select ${r.title}`} />
        </td>
        <td className="cell-title">
          {p.renaming !== null ? (
            <form
              className="inline-form"
              onSubmit={(e) => {
                e.preventDefault();
                p.onRenameSave();
              }}
            >
              <input value={p.renaming} onChange={(e) => p.onRenameChange(e.target.value)} autoFocus aria-label="New title" maxLength={200} />
              <button className="primary small" type="submit">Save</button>
              <button className="small" type="button" onClick={p.onRenameCancel}>Cancel</button>
            </form>
          ) : (
            <button className="row-link" onClick={p.onExpand} aria-expanded={p.expanded}>
              <strong>{r.title}</strong>
              {r.project && <span className="badge">{r.project}</span>}
              {r.preview && r.preview !== r.title && <span className="muted small">{r.preview}</span>}
            </button>
          )}
        </td>
        <td className="mono small">{r.chosen_model ?? r.last_model ?? "—"}</td>
        <td className="num">{r.message_count}</td>
        <td className="num">{num(r.tokens)}</td>
        <td className="small" title={dateTime(r.updated_at)}>{relTime(r.updated_at)}</td>
        <td className="row-actions">
          <button className="icon-btn" onClick={p.onOpen} title="Continue this chat" aria-label={`Continue ${r.title}`}>
            <Icon name="play" size={14} />
          </button>
          <button className="small" onClick={p.onRenameStart}>Rename</button>
          <a className="button small" href={api.exportUrl(r.id, "md")} download title="Export as Markdown">
            <Icon name="download" size={14} /> .md
          </a>
          <a className="button small" href={api.exportUrl(r.id, "json")} download title="Export as JSON">
            .json
          </a>
        </td>
      </tr>
      {p.expanded && (
        <tr className="transcript-row">
          <td colSpan={7}>
            <div className="transcript">
              {p.messages.map((m, i) => (
                <div key={m.id ?? i} className={`t-msg role-${m.role}`}>
                  <span className="t-role">
                    {m.role === "user" ? "You" : m.model === "command" ? "pal (command)" : `pal${m.model ? ` · ${m.model}` : ""}`}
                    {m.created_at && <span className="muted"> · {dateTime(m.created_at)}</span>}
                  </span>
                  {m.role === "user" ? <p className="pre">{m.content}</p> : <Markdown text={m.content} />}
                </div>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
