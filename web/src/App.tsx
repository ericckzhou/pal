import { useCallback, useEffect, useState } from "react";
import { api, ApiError, Me, Pressure, Status as StatusData } from "./api";
import Analytics from "./Analytics";
import Chat from "./Chat";
import Connections from "./Connections";
import ChatRail from "./ChatRail";
import Devices from "./Devices";
import DigestPage from "./Digest";
import { Icon } from "./Icon";
import Login from "./Login";
import Logs from "./Logs";
import Notifications from "./Notifications";
import Schedule from "./Schedule";
import Sessions from "./Sessions";
import SettingsPage from "./SettingsPage";
import Sidebar, { View } from "./Sidebar";
import Status from "./Status";
import TaskBoard from "./TaskBoard";
import { applyTheme, loadTheme, Theme } from "./theme";
import Work, { Section } from "./Work";

const VIEW_KEY = "pal.view";
const PC_ONLY: View[] = ["logs", "settings", "devices", "connections"];

function loadView(): View {
  try {
    const v = localStorage.getItem(VIEW_KEY) as View | null;
    if (v) return v;
  } catch {
    /* storage blocked */
  }
  return "status";
}

// The most severe warning; dismissing it shows the next one. A new or worse warning comes back.
function PressureBanner({ items }: { items: Pressure[] }) {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const top = items.find((p) => !dismissed.has(`${p.level}:${p.kind}`));
  if (!top) return null;
  return (
    <div className={`banner ${top.level}`} role="alert">
      <span>{top.message}</span>
      <button className="icon" aria-label="Dismiss" onClick={() => setDismissed((d) => new Set(d).add(`${top.level}:${top.kind}`))}>
        ×
      </button>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [listVersion, setListVersion] = useState(0);
  const [navOpen, setNavOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [view, setView] = useState<View>(loadView);
  const [inbox, setInbox] = useState(0);
  const [workVersion, setWorkVersion] = useState(0);
  const [chatKey, setChatKey] = useState(0); // bumps only on navigation, so a new chat isn't remounted once saved
  const [newChatModel, setNewChatModel] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusData | null>(null);
  const [theme, setTheme] = useState<Theme>(loadTheme);
  const [projectSection, setProjectSection] = useState<Exclude<Section, "memory" | "documents">>("overview");

  useEffect(() => applyTheme(theme), [theme]);

  // Re-read who we are whenever sign-in state flips (owner PC vs paired device decides what's shown).
  useEffect(() => {
    api
      .me()
      .then((r) => {
        setMe(r);
        setAuthed(r.authenticated);
      })
      .catch(() => setAuthed(false));
  }, [authed]);

  const onAuthError = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) setAuthed(false);
  }, []);

  const refreshInbox = useCallback(() => {
    api.inboxCount().then((r) => setInbox(r.count)).catch(onAuthError);
  }, [onAuthError]);

  useEffect(() => {
    if (!authed) return;
    refreshInbox();
    const t = setInterval(refreshInbox, 60_000);
    return () => clearInterval(t);
  }, [authed, listVersion, refreshInbox]);

  // Health for the sidebar dot and the warning banner. The Status page polls faster while it's open.
  useEffect(() => {
    if (!authed) return;
    const load = () => api.status().then(setStatus).catch(onAuthError);
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [authed, onAuthError]);

  if (authed === null) return <div className="center muted">Loading…</div>;
  if (!authed) return <Login me={me} onDone={() => setAuthed(true)} />;

  const owner = me?.owner ?? false;
  const shown: View = PC_ONLY.includes(view) && !owner ? "status" : view;

  const showView = (v: View) => {
    setView(v);
    setNavOpen(false);
    setRailOpen(false);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* storage blocked */
    }
  };

  const open = (id: string | null) => {
    setActiveId(id);
    setChatKey((k) => k + 1);
    if (id === null) setNewChatModel(null);
    showView("chat");
  };

  const changed = () => setListVersion((v) => v + 1);

  return (
    <div className={`layout ${navOpen ? "nav-open" : ""} ${shown === "chat" ? "with-rail" : ""} ${railOpen ? "rail-open" : ""}`}>
      <Sidebar
        view={shown}
        inbox={inbox}
        owner={owner}
        device={me?.device ?? null}
        version={status?.version ?? null}
        ollamaUp={status ? status.ollama.up : null}
        theme={theme}
        onTheme={setTheme}
        onView={showView}
        onLogout={() => api.logout().finally(() => setAuthed(false))}
      />
      <main className="main">
        <div className="topbar">
          <button className="icon-btn menu-btn" onClick={() => setNavOpen((o) => !o)} aria-label="Menu">
            <Icon name="menu" />
          </button>
          <span className="topbar-title">pal</span>
        </div>
        {status && <PressureBanner items={status.pressure} />}
        <Notifications onAuthError={onAuthError} />
        {shown === "status" ? (
          <Status onOpenConversation={open} onView={showView} onStatus={setStatus} onAuthError={onAuthError} owner={owner} />
        ) : shown === "sessions" ? (
          <Sessions version={listVersion} onOpenConversation={open} onChanged={changed} onAuthError={onAuthError} />
        ) : shown === "analytics" ? (
          <Analytics onAuthError={onAuthError} />
        ) : shown === "digest" ? (
          <DigestPage onView={showView} onAuthError={onAuthError} />
        ) : shown === "schedule" ? (
          <Schedule version={workVersion} owner={owner} onAuthError={onAuthError} />
        ) : shown === "logs" ? (
          <Logs onAuthError={onAuthError} />
        ) : shown === "settings" ? (
          <SettingsPage onAuthError={onAuthError} onSaved={() => api.status().then(setStatus).catch(onAuthError)} />
        ) : shown === "devices" ? (
          <Devices remoteUrl={me?.remote_url ?? null} onAuthError={onAuthError} />
        ) : shown === "connections" ? (
          <Connections onAuthError={onAuthError} />
        ) : shown === "tasks" ? (
          <TaskBoard version={workVersion} onAuthError={onAuthError} />
        ) : shown === "work" || shown === "memory" || shown === "documents" ? (
          <Work
            version={workVersion}
            section={shown === "work" ? projectSection : shown}
            inbox={inbox}
            onSection={(s) => {
              // Memory and Documents keep their own views so other pages can link straight to them.
              if (s === "memory" || s === "documents") showView(s);
              else {
                setProjectSection(s);
                showView("work");
              }
            }}
            onInboxChanged={refreshInbox}
            onAuthError={onAuthError}
            onOpenConversation={open}
            onView={showView}
          />
        ) : (
          <Chat
            key={chatKey}
            convId={activeId}
            onCreated={(id) => setActiveId(id)}
            onChanged={changed}
            onWorkChanged={() => setWorkVersion((v) => v + 1)}
            workVersion={workVersion}
            onAuthError={onAuthError}
            actions={{ onOpenConversation: open, onOpenMemory: () => showView("memory") }}
            newChatModel={newChatModel}
            onNewChatModel={setNewChatModel}
            listVersion={listVersion}
            onToggleRail={() => setRailOpen((o) => !o)}
          />
        )}
      </main>
      {shown === "chat" && (
        <ChatRail
          activeId={activeId}
          version={listVersion}
          open={railOpen}
          onOpen={open}
          onDeleted={(id) => {
            if (id === activeId) open(null);
            changed();
          }}
          onClose={() => setRailOpen(false)}
          onAuthError={onAuthError}
        />
      )}
      <button className="scrim" aria-label="Close" tabIndex={-1} onClick={() => { setNavOpen(false); setRailOpen(false); }} />
    </div>
  );
}
