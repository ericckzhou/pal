import { useCallback, useEffect, useRef, useState } from "react";
import { api, Notification as Alert } from "./api";

const POLL_MS = 30_000;
const SHOWN_KEY = "pal-notified";

function shownIds(): Set<string> {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(SHOWN_KEY) ?? "[]"));
  } catch {
    return new Set();
  }
}

function remember(ids: Set<string>) {
  try {
    sessionStorage.setItem(SHOWN_KEY, JSON.stringify([...ids].slice(-200)));
  } catch {
    /* storage unavailable: worst case a desktop notification repeats */
  }
}

/** Fired reminders: a banner in the app, plus a desktop notification when the browser allows it. */
export default function Notifications({ onAuthError }: { onAuthError: (err: unknown) => void }) {
  const [items, setItems] = useState<Alert[]>([]);
  const [permission, setPermission] = useState(() => ("Notification" in window ? Notification.permission : "denied"));
  const shown = useRef(shownIds());

  const load = useCallback(() => {
    api
      .notifications()
      .then((list) => {
        setItems(list);
        const fresh = list.filter((n) => !shown.current.has(n.id) && Date.now() - new Date(n.fired_at).getTime() < 15 * 60_000);
        if ("Notification" in window && Notification.permission === "granted") {
          for (const n of fresh) new Notification("pal reminder", { body: n.title, tag: n.id });
        }
        list.forEach((n) => shown.current.add(n.id));
        remember(shown.current);
      })
      .catch(onAuthError);
  }, [onAuthError]);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const act = (fn: () => Promise<unknown>) => fn().then(load).catch(onAuthError);
  const ask = () => Notification.requestPermission().then(setPermission);

  if (items.length === 0) {
    return permission === "default" ? (
      <div className="alerts">
        <button className="link small" onClick={ask}>
          Turn on desktop notifications for reminders
        </button>
      </div>
    ) : null;
  }
  return (
    <div className="alerts" role="status" aria-live="polite">
      {items.slice(0, 5).map((n) => (
        <div key={n.id} className="alert">
          <span>
            ⏰ <strong>{n.title}</strong> <span className="muted small">{new Date(n.fired_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
          </span>
          <span className="memory-meta">
            {n.reminder_id && (
              <button className="small" onClick={() => act(() => api.snooze(n.id, 10))}>
                Snooze 10 min
              </button>
            )}
            <button className="small" onClick={() => act(() => api.dismiss(n.id))}>
              Done
            </button>
          </span>
        </div>
      ))}
      {items.length > 1 && (
        <button className="link small" onClick={() => act(() => api.dismissAll())}>
          Dismiss all ({items.length})
        </button>
      )}
      {permission === "default" && (
        <button className="link small" onClick={ask}>
          Turn on desktop notifications
        </button>
      )}
    </div>
  );
}
