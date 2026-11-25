import { FormEvent, useState } from "react";
import { api, ApiError, Me } from "./api";
import { createPasskey, getPasskey, passkeyMessage, passkeysSupported } from "./webauthn";

function codeFromHash(): string {
  // ?pair= survives QR scanners that drop the #fragment; #pair= still works for older links.
  const m = (window.location.search + window.location.hash).match(/pair=([A-Za-z0-9-]+)/);
  return m ? m[1] : "";
}

const message = (err: unknown) => (err instanceof ApiError ? err.message : passkeyMessage(err));

export default function Login({ me, onDone }: { me: Me | null; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState(codeFromHash);
  const [pairing, setPairing] = useState(() => codeFromHash() !== "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const passwordLogin = me?.password_login ?? true;
  const passkeys = !passwordLogin && passkeysSupported(); // the PC uses its password; passkeys are per paired device

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      if (window.location.hash || window.location.search) history.replaceState(null, "", window.location.pathname);
      onDone();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  const signInWithPassword = (e: FormEvent) => {
    e.preventDefault();
    run(() => api.login(password));
  };

  const signInWithPasskey = () =>
    run(async () => {
      const { flow, options } = await api.passkeyStart();
      await api.passkeyFinish(flow, await getPasskey(options));
    });

  const pair = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      const { flow, options } = await api.pairStart(code);
      await api.pairFinish(flow, await createPasskey(options));
    });
  };

  return (
    <div className="login">
      <h1>pal</h1>
      {passkeys && !pairing && (
        <button className="primary" disabled={busy} onClick={signInWithPasskey}>
          Sign in with passkey
        </button>
      )}
      {passkeys &&
        (pairing ? (
          <form className="login-form" onSubmit={pair}>
            <p className="muted small">
              Enter the pairing code shown on the PC (Devices → Add device). This device then gets its own passkey.
            </p>
            <input
              autoFocus={!code}
              autoComplete="one-time-code"
              autoCapitalize="characters"
              placeholder="ABCD-EFGH"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            <button className="primary" disabled={busy || code.replace(/[^A-Za-z0-9]/g, "").length !== 8}>
              {busy ? "Pairing…" : "Pair this device"}
            </button>
            <button type="button" className="link" onClick={() => setPairing(false)}>
              Back
            </button>
          </form>
        ) : (
          <button className="link" onClick={() => setPairing(true)}>
            New device? Pair it with a code
          </button>
        ))}
      {!passkeys && !passwordLogin && (
        <p className="muted small">Passkeys need a secure connection. Open pal at its https tailnet address.</p>
      )}
      {passwordLogin && (
        <form className="login-form" onSubmit={signInWithPassword}>
          <input
            type="password"
            autoFocus
            autoComplete="current-password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <button className="primary" disabled={busy || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
