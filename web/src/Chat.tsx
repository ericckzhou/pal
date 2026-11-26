import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { api, isClaude, Message, Project, Proposal, Step, streamReply, THINKING_LEVELS, ThinkingLevel } from "./api";
import { num } from "./format";
import { Icon } from "./Icon";
import ModelPicker from "./ModelPicker";
import { Markdown } from "./Markdown";
import { ProposalCard } from "./Proposals";
import { SourceActions, SourceList } from "./Sources";

type Props = {
  convId: string | null;
  onCreated: (id: string) => void;
  onChanged: () => void;
  onWorkChanged: () => void;
  onAuthError: (err: unknown) => void;
  actions: SourceActions;
  workVersion: number;
  newChatModel: string | null;
  onNewChatModel: (m: string | null) => void;
  listVersion: number;
  onToggleRail: () => void;
};

const STEP_ICON: Record<Step["kind"], string> = { retrieval: "book", memory: "memory", proposal: "bolt", command: "terminal" };

// What pal did before replying: lookups, saved memories, drafted actions, slash commands.
function StepCards({ steps }: { steps: Step[] }) {
  return (
    <div className="steps">
      {steps.map((s, i) => (
        <div key={i} className={`step step-${s.kind}`}>
          <Icon name={STEP_ICON[s.kind] ?? "bolt"} size={14} />
          <span className="step-label">{s.label}</span>
          {s.detail && <span className="step-detail">{s.detail}</span>}
        </div>
      ))}
    </div>
  );
}

function Reasoning({ text, live }: { text: string; live: boolean }) {
  // Open while the model is still reasoning; folds away once the reply starts. Not kept after a reload.
  return (
    <details className="reasoning small" open={live}>
      <summary>{live ? "Thinking…" : "Thought it through"}</summary>
      <div className="reasoning-text">{text}</div>
    </details>
  );
}

function ReplyMeta({ m }: { m: Message }) {
  if (!m.model || m.model === "command") return null;
  const tokens = (m.prompt_tokens ?? 0) + (m.completion_tokens ?? 0);
  return (
    <p className="reply-meta">
      {m.model}
      {tokens > 0 && (
        <span title={`${m.prompt_tokens ?? 0} read, ${m.completion_tokens ?? 0} written`}> · {num(tokens)} tokens</span>
      )}
    </p>
  );
}

export default function Chat({
  convId, onCreated, onChanged, onWorkChanged, onAuthError, actions, workVersion, newChatModel, onNewChatModel, listVersion, onToggleRail,
}: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [suggesting, setSuggesting] = useState(false);
  const [model, setModel] = useState<string | null>(null);
  const [thinking, setThinking] = useState<ThinkingLevel | null>(null);
  const idRef = useRef<string | null>(convId);
  const endRef = useRef<HTMLDivElement>(null);

  const loadConversation = (id: string) =>
    Promise.all([api.get(id), api.proposals(id)])
      .then(([c, ps]) => {
        setMessages(c.messages);
        setProjectId(c.project_id ?? "");
        setProposals(ps);
      })
      .catch(onAuthError);

  // The rail can switch this chat's model, so re-read it whenever the chat list changes.
  useEffect(() => {
    if (convId)
      api.get(convId).then((c) => { setModel(c.model ?? null); setThinking(c.thinking ?? null); }).catch(onAuthError);
    else {
      setModel(newChatModel);
      setThinking(null);
    }
  }, [convId, listVersion, newChatModel, onAuthError]);

  useEffect(() => {
    if (convId) loadConversation(convId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId, onAuthError]);

  useEffect(() => {
    api.projects().then(setProjects).catch(onAuthError);
  }, [workVersion, onAuthError]);

  // Braces matter: newer Chrome returns a Promise from scrollIntoView, and React would call it as a cleanup.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, proposals]);

  const ensureConversation = async () => {
    if (!idRef.current) {
      const id = (await api.create()).id;
      if (newChatModel || thinking)
        await api.patchConversation(id, { ...(newChatModel ? { model: newChatModel } : {}), ...(thinking ? { thinking } : {}) });
      idRef.current = id;
    }
    return idRef.current;
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    setDraft("");
    setMessages((m) => [...m, { role: "user", content: text }, { role: "assistant", content: "" }]);
    let workChanged = false;
    try {
      const id = await ensureConversation();
      for await (const ev of streamReply(id, text)) {
        if (ev.type === "step") {
          setMessages((m) => {
            const last = m[m.length - 1];
            return [...m.slice(0, -1), { ...last, steps: [...(last.steps ?? []), ev.step] }];
          });
        } else if (ev.type === "done") {
          setMessages((m) => [
            ...m.slice(0, -1),
            { ...m[m.length - 1], id: ev.message_id, model: ev.model, ...(ev.usage ?? {}) },
          ]);
        } else if (ev.type === "memory_saved") {
          setNotice(`Saved to memory: “${ev.memory.text}”`);
        } else if (ev.type === "sources") {
          setMessages((m) => [...m.slice(0, -1), { ...m[m.length - 1], sources: ev.sources }]);
        } else if (ev.type === "thinking") {
          setMessages((m) => [...m.slice(0, -1), { ...m[m.length - 1], thinking: (m[m.length - 1].thinking ?? "") + ev.text }]);
        } else if (ev.type === "thinking_changed") {
          setThinking(ev.thinking);
        } else if (ev.type === "token") {
          setMessages((m) => [...m.slice(0, -1), { ...m[m.length - 1], content: m[m.length - 1].content + ev.text }]);
        } else if (ev.type === "proposal") {
          setProposals((ps) => [...ps, ev.proposal]);
        } else if (ev.type === "work_changed") {
          workChanged = true;
        } else if (ev.type === "model_changed") {
          setModel(ev.model);
          onChanged(); // the rail's picker and list show the new model
        } else if (ev.type === "error") {
          setError(ev.message);
          setMessages((m) => m.slice(0, -1));
        }
      }
      if (workChanged) {
        onWorkChanged();
        api.get(id).then((c) => setProjectId(c.project_id ?? "")).catch(onAuthError);
      }
    } catch (err) {
      onAuthError(err);
      setError("Message not sent. Check that pal is running.");
      setMessages((m) => m.slice(0, -1));
    } finally {
      setBusy(false);
      onChanged();
      if (!convId && idRef.current) onCreated(idRef.current);
    }
  };

  const changeProject = async (value: string) => {
    setError("");
    try {
      const id = await ensureConversation();
      await api.setFocus(id, value || null);
      setProjectId(value);
      if (!convId) onCreated(id);
      onChanged();
    } catch (err) {
      onAuthError(err);
      setError("Could not change the project.");
    }
  };

  const pickModel = async (value: string | null) => {
    setError("");
    if (!idRef.current) {
      onNewChatModel(value); // a new chat applies it when it's created
      return;
    }
    try {
      await api.patchConversation(idRef.current, { model: value });
      setModel(value);
      onChanged(); // the chat list shows the new model
    } catch (err) {
      onAuthError(err);
      setError(err instanceof Error ? err.message : "Could not change the model.");
    }
  };

  const changeThinking = async (value: string) => {
    const level = (value || null) as ThinkingLevel | null;
    setThinking(level);
    if (!idRef.current) return; // a new chat applies it when it's created
    try {
      await api.patchConversation(idRef.current, { thinking: level });
    } catch (err) {
      onAuthError(err);
      setError("Could not change the thinking level.");
    }
  };

  const suggest = async () => {
    if (!idRef.current) return;
    setSuggesting(true);
    setError("");
    try {
      const n = (await api.suggestMemories(idRef.current)).proposed.length;
      setNotice(n ? `${n} suggestion${n > 1 ? "s" : ""} added to your Memory inbox.` : "Nothing new worth remembering.");
      onChanged();
    } catch (err) {
      onAuthError(err);
      setError(err instanceof Error ? err.message : "Could not suggest memories.");
    } finally {
      setSuggesting(false);
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  return (
    <section className="chat">
      <div className="chat-bar small">
        <button className="icon-btn rail-toggle" onClick={onToggleRail} aria-label="Show chats" title="Chats">
          <Icon name="rail" />
        </button>
      </div>
      {isClaude(model) && (
        <p className="cloud-banner small" role="status">
          <Icon name="bolt" size={14} /> <strong>{model}</strong>: this chat's messages are sent to Anthropic. Memories,
          documents and notes are not.
        </p>
      )}
      <div className="messages">
        {messages.length === 0 && (
          <p className="center muted">
            What's on your mind?
            <br />
            <span className="small">Type /help for commands like /task and /remind.</span>
          </p>
        )}
        {messages.map((m, i) => (
          <div key={m.id ?? i} className={`msg ${m.role}`}>
            {m.role === "user" ? (
              m.content
            ) : (
              <>
                {m.steps?.length ? <StepCards steps={m.steps} /> : null}
                {m.thinking ? <Reasoning text={m.thinking} live={!m.content} /> : null}
                {m.content ? (
                  <>
                    <Markdown text={m.content} cites={new Set(m.sources?.map((s) => s.handle))} anchor={`${i}-`} />
                    {m.sources?.length ? (
                      <SourceList sources={m.sources} text={m.content} actions={actions} anchor={`${i}-`} />
                    ) : null}
                    <ReplyMeta m={m} />
                  </>
                ) : (
                  <span className="muted typing">Thinking…</span>
                )}
              </>
            )}
          </div>
        ))}
        {proposals.map((p) => (
          <ProposalCard
            key={p.id}
            proposal={p}
            projects={projects}
            onDone={(message) => {
              setProposals((ps) => ps.filter((x) => x.id !== p.id));
              setNotice(message);
              onWorkChanged();
            }}
            onError={(err) => {
              onAuthError(err);
              setError(err instanceof Error ? err.message : "Could not save that.");
            }}
          />
        ))}
        <div ref={endRef} />
      </div>
      {error && <p className="error">{error}</p>}
      {notice && <p className="notice small">{notice}</p>}
      <div className="chat-controls small">
        {projects.length > 0 && (
          <label className="picker">
            Project{" "}
            <select value={projectId} onChange={(e) => changeProject(e.target.value)} aria-label="This chat's project">
              <option value="">None</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="picker" title="How much the model reasons before replying (/think)">
          Thinking{" "}
          <select value={thinking ?? ""} onChange={(e) => changeThinking(e.target.value)} aria-label="Thinking level">
            <option value="">Default</option>
            {THINKING_LEVELS.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </label>
        <ModelPicker value={model} onPick={pickModel} onAuthError={onAuthError} />
      </div>
      <div className="composer">
        <textarea
          rows={2}
          placeholder="Message pal (Enter to send, Shift+Enter for a new line)"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          autoFocus
        />
        {messages.length > 0 && (
          <button className="small" onClick={suggest} disabled={busy || suggesting} title="Ask pal what's worth remembering from this chat">
            {suggesting ? "Thinking…" : "Suggest memories"}
          </button>
        )}
        <button className="primary" onClick={send} disabled={busy || !draft.trim()}>
          {busy ? "…" : "Send"}
        </button>
      </div>
    </section>
  );
}
