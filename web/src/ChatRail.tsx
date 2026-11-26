import { useEffect, useState } from "react";
import { api, Conversation, SearchHit } from "./api";
import { relTime } from "./format";
import { Icon } from "./Icon";
import { Snippet } from "./Markdown";

type Props = {
  activeId: string | null;
  version: number;
  open: boolean;
  onOpen: (id: string | null) => void;
  onDeleted: (id: string) => void;
  onClose: () => void;
  onAuthError: (err: unknown) => void;
};

// The chat page's own conversation list, beside the messages, newest first. The model is picked above the input.
export default function ChatRail({
  activeId, version, open, onOpen, onDeleted, onClose, onAuthError,
}: Props) {
  const [items, setItems] = useState<Conversation[]>([]);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    api.list().then(setItems).catch(onAuthError);
  }, [version, activeId, tick, onAuthError]);

  useEffect(() => {
    if (!query.trim()) {
      setHits(null);
      return;
    }
    const t = setTimeout(() => api.search(query).then(setHits).catch(onAuthError), 250);
    return () => clearTimeout(t);
  }, [query, onAuthError]);

  const remove = async (id: string) => {
    try {
      await api.remove(id);
      setConfirmId(null);
      onDeleted(id);
    } catch (err) {
      onAuthError(err);
    }
  };

  return (
    <aside className={`rail ${open ? "open" : ""}`} aria-label="Conversations">
      <div className="rail-head">
        <div className="rail-actions">
          <button className="primary" onClick={() => onOpen(null)}>
            <Icon name="plus" /> New chat
          </button>
          <button className="icon-btn" onClick={() => setTick((t) => t + 1)} aria-label="Refresh list" title="Refresh">
            <Icon name="refresh" />
          </button>
          <button className="icon-btn rail-close" onClick={onClose} aria-label="Close list">
            ×
          </button>
        </div>
        <input className="search" placeholder="Search chats" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      <nav className="list">
        {hits !== null ? (
          hits.length === 0 ? (
            <p className="muted small">No matches.</p>
          ) : (
            hits.map((h, i) => (
              <button key={i} className="hit" onClick={() => onOpen(h.conversation_id)}>
                <strong>{h.title}</strong>
                <span className="small">
                  <Snippet text={h.snippet} />
                </span>
              </button>
            ))
          )
        ) : items.length === 0 ? (
          <p className="muted small">No chats yet.</p>
        ) : (
          items.map((c) => (
            <div key={c.id} className={`item ${c.id === activeId ? "active" : ""}`}>
              <button className="title" onClick={() => onOpen(c.id)} title={c.title}>
                <span className="item-title">{c.title}</span>
                <span className="item-meta">
                  {relTime(c.updated_at)} · {c.message_count ?? 0} msg{c.model ? ` · ${c.model}` : ""}
                </span>
              </button>
              {confirmId === c.id ? (
                <span className="confirm">
                  <button className="danger small" onClick={() => remove(c.id)}>
                    Delete
                  </button>
                  <button className="small" onClick={() => setConfirmId(null)}>
                    Keep
                  </button>
                </span>
              ) : (
                <button className="icon" aria-label={`Delete ${c.title}`} onClick={() => setConfirmId(c.id)}>
                  ×
                </button>
              )}
            </div>
          ))
        )}
      </nav>
    </aside>
  );
}
