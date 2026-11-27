import { Icon } from "./Icon";
import { Theme, THEMES } from "./theme";

export type View =
  | "status"
  | "chat"
  | "sessions"
  | "work"
  | "tasks"
  | "memory"
  | "documents"
  | "schedule"
  | "digest"
  | "analytics"
  | "logs"
  | "settings"
  | "devices"
  | "connections";

type Item = { view: View; label: string; pcOnly?: boolean };
// Memory and Documents live inside Projects; their nav entry stays lit while one of them is open.
const navOf = (v: View): View => (v === "memory" || v === "documents" ? "work" : v);
const GROUPS: { title: string; items: Item[] }[] = [
  {
    title: "Assistant",
    items: [
      { view: "chat", label: "Chat" },
      { view: "work", label: "Projects" },
      { view: "tasks", label: "Tasks" },
      { view: "schedule", label: "Schedule" },
      { view: "digest", label: "Weekly digest" },
    ],
  },
  {
    title: "Manage",
    items: [
      { view: "status", label: "Status" },
      { view: "sessions", label: "Sessions" },
      { view: "analytics", label: "Analytics" },
      { view: "logs", label: "Logs", pcOnly: true },
      { view: "settings", label: "Settings", pcOnly: true },
      { view: "devices", label: "Devices", pcOnly: true },
      { view: "connections", label: "Connections", pcOnly: true },
    ],
  },
];

type Props = {
  view: View;
  inbox: number;
  owner: boolean;
  device: string | null;
  version: string | null;
  ollamaUp: boolean | null;
  theme: Theme;
  onTheme: (t: Theme) => void;
  onView: (v: View) => void;
  onLogout: () => void;
};

export default function Sidebar({ view: current, inbox, owner, device, version, ollamaUp, theme, onTheme, onView, onLogout }: Props) {
  const view = navOf(current);
  return (
    <aside className="sidebar" aria-label="Pages">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          <Icon name="terminal" size={18} />
        </span>
        <span className="brand-name">pal</span>
        {version && <span className="brand-version">v{version}</span>}
        <span
          className={`dot ${ollamaUp === null ? "" : ollamaUp ? "ok" : "bad"}`}
          title={ollamaUp === null ? "Checking the model…" : ollamaUp ? "Model ready" : "Model not responding"}
          aria-label={ollamaUp === false ? "Model not responding" : "Model ready"}
        />
      </div>
      <nav className="nav-groups">
        {GROUPS.map((g) => (
          <div key={g.title} className="nav-group">
            <p className="nav-title">{g.title}</p>
            {g.items
              .filter((it) => !it.pcOnly || owner)
              .map((it) => (
                <button
                  key={it.view}
                  className={`nav ${view === it.view ? "active" : ""}`}
                  aria-current={view === it.view ? "page" : undefined}
                  onClick={() => onView(it.view)}
                >
                  <Icon name={it.view} />
                  <span className="nav-label">{it.label}</span>
                  {it.view === "work" && inbox > 0 && (
                    <span className="count" aria-label={`${inbox} suggestions waiting`}>
                      {inbox}
                    </span>
                  )}
                </button>
              ))}
          </div>
        ))}
      </nav>
      <div className="sidebar-foot">
        <label className="small muted theme-pick">
          Theme
          <select value={theme} onChange={(e) => onTheme(e.target.value as Theme)} aria-label="Theme">
            {THEMES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <button className="link small" onClick={onLogout}>
          Sign out{device ? ` (${device})` : ""}
        </button>
      </div>
    </aside>
  );
}
