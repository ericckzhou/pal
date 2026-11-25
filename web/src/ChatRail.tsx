import { useEffect, useState } from "react";
import { api, Conversation, isClaude, ModelList, SearchHit } from "./api";
import { relTime } from "./format";
import { Icon } from "./Icon";
import { Snippet } from "./Markdown";

type Props = {
  activeId: string | null;
  version: number;
  open: boolean;
  newChatModel: string | null;
  onNewChatModel: (m: string | null) => void;
  onOpen: (id: string | null) => void;
  onDeleted: (id: string) => void;
  onChanged: () => void;
  onClose: () => void;
  onAuthError: (err: unknown) => void;
};

// The chat page's own conversation list, beside the messages: model on top, newest chats below.
export default function ChatRail({
  activeId, version, open, newChatModel, onNewChatModel, onOpen, onDeleted, onChanged, onClose, onAuthError,
}: Props) {
  const [items, setItems] = useState<Conversation[]>([]);
  const [models, setModels] = useState<ModelList | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    api.list().then(setItems).catch(onAuthError);
  }, [version, activeId, tick, onAuthError]);

  useEffect(() => {
    api.models().then(setModels).catch(onAuthError);
  }, [tick, onAuthError]);

  useEffect(() => {
    if (!query.trim()) {
      setHits(null);
      return;
    }
    const t = setTimeout(() => api.search(query).then(setHits).catch(onAuthError), 250);
    return () => clearTimeout(t);
  }, [query, onAuthError]);

  const active = items.find((c) => c.id === activeId);
  const [pendingClaude, setPendingClaude] = useState<string | null>(null);
  const chosen = activeId ? active?.model ?? "" : newChatModel ?? "";

  const pickModel = async (value: string) => {
    setError("");
    if (isClaude(value) && !isClaude(chosen) && pendingClaude !== value) {
      setPendingClaude(value);  // opt-in: the owner confirms before this chat's text leaves the PC
      return;
    }
    setPendingClaude(null);
    if (!activeId) {
      onNewChatModel(value || null);
      return;
    }
    try {
      await api.patchConversation(activeId, { model: value || null });
      setItems((xs) => xs.map((c) => (c.id === activeId ? { ...c, model: value || null } : c)));
      onChanged();
    } catch (err) {
      onAuthError(err);
      setError(err instanceof Error ? err.message : "Could not change the model.");
    }
  };

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
        <label className="field">
          <span className="field-label">Model</span>
          <select value={chosen} onChange={(e) => pickModel(e.target.value)} aria-label="Model for this chat">
            <option value="">Default{models ? ` (${models.default})` : ""}</option>
            <optgroup label="On this PC">
              {models?.models.filter((m) => m.provider !== "claude").map((m) => (
                <option key={m.name} value={m.name} disabled={m.installed === false}>
                  {m.name}
                  {m.installed === false ? " (not installed)" : ""}
                </option>
              ))}
            </optgroup>
            <optgroup label="Claude (sends this chat to Anthropic)">
              {models?.models.filter((m) => m.provider === "claude").map((m) => (
                <option key={m.name} value={m.name} disabled={!m.installed}>
                  {m.name}
                  {!m.installed ? " (no API key)" : ""}
                </option>
              ))}
            </optgroup>
          </select>
        </label>
        {pendingClaude && (
          <div className="cloud-confirm small" role="alert">
            <p>
              Use <strong>{pendingClaude}</strong> for this chat? Its messages will be sent to Anthropic. Memories,
              documents and notes stay on this PC.
            </p>
            <div className="row-actions">
              <button className="primary small" onClick={() => pickModel(pendingClaude)}>Use Claude</button>
              <button className="small" onClick={() => setPendingClaude(null)}>Cancel</button>
            </div>
          </div>
        )}
        {models && !models.ollama_up && <p className="error small">Ollama isn't responding.</p>}
        {error && <p className="error small">{error}</p>}
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
