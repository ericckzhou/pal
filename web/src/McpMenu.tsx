import { KeyboardEvent, useEffect, useRef, useState } from "react";

/** One MCP server a chat may use. `warning` replaces `detail` when it's on but something else is still needed. */
export type McpServer = { id: string; name: string; on: boolean; detail: string; warning?: string };

/**
 * The chat's MCP servers: a button showing how many are on, opening a menu with one switch per server.
 * Switches don't close the menu. Escape or a click outside closes it; Escape returns focus to the button.
 */
export function McpMenu({ servers, onToggle }: { servers: McpServer[]; onToggle: (id: string, on: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const active = servers.filter((s) => s.on).length;

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape" && open) {
      e.stopPropagation();
      setOpen(false);
      button.current?.focus();
      return;
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const items = Array.from(wrap.current?.querySelectorAll<HTMLButtonElement>("[role=menuitemcheckbox]") ?? []);
    if (!items.length) return;
    e.preventDefault();
    const at = items.indexOf(document.activeElement as HTMLButtonElement);
    const next = e.key === "ArrowDown" ? (at + 1) % items.length : (at - 1 + items.length) % items.length;
    items[next].focus();
  };

  return (
    <div className="menu-wrap mcp-wrap" ref={wrap} onKeyDown={onKey}>
      <button
        ref={button}
        type="button"
        className={`small mcp-button${active ? " on" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        title="MCP servers this chat may use"
        onClick={() => setOpen((o) => !o)}
      >
        MCP{active > 0 && <span className="mcp-count">{active}</span>}
      </button>
      {open && (
        <div className="menu up mcp-menu" role="menu" aria-label="MCP servers for this chat">
          <p className="mcp-head small muted">MCP servers for this chat</p>
          {servers.map((s, i) => (
            <button
              key={s.id}
              type="button"
              role="menuitemcheckbox"
              aria-checked={s.on}
              autoFocus={i === 0}
              onClick={() => onToggle(s.id, !s.on)}
            >
              <span className={`mcp-switch${s.on ? " on" : ""}`} aria-hidden />
              <span className="mcp-text">
                <span>{s.name}</span>
                <span className={`small ${s.on && s.warning ? "mcp-warn" : "muted"}`}>{s.on && s.warning ? s.warning : s.detail}</span>
              </span>
            </button>
          ))}
          <p className="mcp-foot small muted">Set servers up on the Connections page, on this PC.</p>
        </div>
      )}
    </div>
  );
}
