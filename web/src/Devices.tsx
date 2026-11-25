import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import qrcode from "qrcode-generator";
import { api, ApiError, Device, DeviceKind, PairingCode } from "./api";

type Props = { remoteUrl: string | null; onAuthError: (err: unknown) => void };

function QR({ text }: { text: string }) {
  const cells = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(text);
    qr.make();
    const n = qr.getModuleCount();
    const dark: string[] = [];
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) dark.push(`M${c} ${r}h1v1h-1z`);
    return { n, path: dark.join("") };
  }, [text]);
  return (
    <svg className="qr" viewBox={`-4 -4 ${cells.n + 8} ${cells.n + 8}`} role="img" aria-label="Pairing QR code">
      <rect x="-4" y="-4" width={cells.n + 8} height={cells.n + 8} fill="#fff" />
      <path d={cells.path} fill="#000" />
    </svg>
  );
}

function when(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export default function Devices({ remoteUrl, onAuthError }: Props) {
  const [items, setItems] = useState<Device[]>([]);
  const [name, setName] = useState("Phone");
  const [kind, setKind] = useState<DeviceKind>("phone");
  const [pending, setPending] = useState<PairingCode | null>(null);
  const [before, setBefore] = useState<Set<string>>(new Set());
  const [justPaired, setJustPaired] = useState("");
  const [now, setNow] = useState(Date.now());
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => api.devices().then(setItems).catch(onAuthError), [onAuthError]);
  useEffect(() => {
    load();
  }, [load]);

  // The code is spent once its device shows up; drop it so it is never shown again.
  useEffect(() => {
    const fresh = pending && items.find((d) => !d.revoked_at && !before.has(d.id));
    if (fresh) {
      setJustPaired(fresh.name);
      setPending(null);
    }
  }, [items, pending, before]);

  // While a code is live, watch for the device to show up and count down.
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(() => {
      setNow(Date.now());
      load();
    }, 3000);
    return () => clearInterval(t);
  }, [pending, load]);

  const secondsLeft = pending ? Math.max(0, Math.round((Date.parse(pending.expires_at) - now) / 1000)) : 0;

  const addDevice = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      const code = await api.newPairingCode(name, kind);
      setBefore(new Set(items.map((d) => d.id)));
      setPending(code);
      setJustPaired("");
      setNow(Date.now());
    } catch (err) {
      if (err instanceof ApiError && err.status !== 401) setError(err.message);
      else onAuthError(err);
    }
  };

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      setConfirmId(null);
      load();
    } catch (err) {
      onAuthError(err);
    }
  };

  const live = items.filter((d) => !d.revoked_at);
  const revoked = items.filter((d) => d.revoked_at);

  return (
    <section className="memory">
      <div className="memory-head">
        <h2>Devices</h2>
      </div>
      {!remoteUrl && (
        <p className="alert">
          Tailscale isn't running on this PC, so other devices can't reach pal yet. Install Tailscale, run{" "}
          <code>tailscale serve --bg http://127.0.0.1:8765</code>, then restart pal.
        </p>
      )}
      {remoteUrl && <p className="muted small">Other devices reach pal at {remoteUrl} over Tailscale.</p>}

      {pending && secondsLeft > 0 ? (
        <div className="pairing">
          {pending.url && <QR text={pending.url} />}
          <div className="pairing-text">
            <p>
              On <strong>{pending.name}</strong>, {pending.url ? "scan this code or open the link" : "open pal"} and enter:
            </p>
            <p className="pair-code">{pending.code}</p>
            {pending.url && <p className="small muted pre">{pending.url}</p>}
            <p className="small muted">
              Works once, for {Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, "0")} more. The device must be on your
              tailnet.
            </p>
            <button onClick={() => setPending(null)}>Cancel</button>
          </div>
        </div>
      ) : (
        <>
          {justPaired && <p className="notice">{justPaired} is paired.</p>}
          <form className="memory-add" onSubmit={addDevice}>
            <input placeholder="Device name" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
            <select value={kind} onChange={(e) => setKind(e.target.value as DeviceKind)} aria-label="Device kind">
              <option value="phone">Phone</option>
              <option value="laptop">Laptop</option>
              <option value="tablet">Tablet</option>
              <option value="other">Other</option>
            </select>
            <button className="primary" disabled={!name.trim()}>
              Add device
            </button>
          </form>
        </>
      )}
      {error && <p className="error">{error}</p>}

      <ul className="memory-list">
        <li className="memory-item">
          <p className="memory-text">
            <strong>This PC</strong> <span className="badge stated">password</span>
          </p>
          <p className="small muted">Manages devices. Can't be revoked from here; use “pal set-password” to sign out everywhere.</p>
        </li>
        {live.map((d) => (
          <li key={d.id} className="memory-item">
            <p className="memory-text">
              <strong>{d.name}</strong> <span className="badge">{d.kind}</span>
            </p>
            <div className="memory-meta small muted">
              <span>Paired {when(d.created_at)} · last used {when(d.last_seen_at)}</span>
              <span className="spacer" />
              {confirmId === d.id ? (
                <>
                  <button className="danger" onClick={() => act(() => api.revokeDevice(d.id))}>
                    Revoke now
                  </button>
                  <button onClick={() => setConfirmId(null)}>Keep</button>
                </>
              ) : (
                <button className="danger" onClick={() => setConfirmId(d.id)}>
                  Revoke
                </button>
              )}
            </div>
          </li>
        ))}
        {revoked.map((d) => (
          <li key={d.id} className="memory-item revoked">
            <p className="memory-text">
              {d.name} <span className="badge">revoked {when(d.revoked_at)}</span>
            </p>
            <div className="memory-meta">
              <span className="spacer" />
              <button className="link small" onClick={() => act(() => api.forgetDevice(d.id))}>
                Remove from list
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
