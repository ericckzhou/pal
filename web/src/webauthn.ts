// Passkey ceremonies. The server sends WebAuthn options as JSON (binary fields base64url); the browser API wants
// ArrayBuffers, and the server wants the credential back as JSON. Done by hand: older Safari lacks the JSON helpers.

type Json = Record<string, any>;

function fromB64url(s: string): ArrayBuffer {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

function toB64url(buf: ArrayBuffer | null | undefined): string | undefined {
  if (!buf) return undefined;
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

const descriptors = (list: Json[] | undefined) => (list ?? []).map((c) => ({ ...c, id: fromB64url(c.id) }));

export function passkeysSupported(): boolean {
  return window.isSecureContext && typeof window.PublicKeyCredential === "function";
}

export async function createPasskey(options: Json): Promise<Json> {
  const publicKey: PublicKeyCredentialCreationOptions = {
    ...options,
    challenge: fromB64url(options.challenge),
    user: { ...options.user, id: fromB64url(options.user.id) },
    excludeCredentials: descriptors(options.excludeCredentials),
  } as PublicKeyCredentialCreationOptions;
  const cred = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential | null;
  if (!cred) throw new Error("No passkey was created.");
  const r = cred.response as AuthenticatorAttestationResponse;
  return {
    id: cred.id,
    rawId: toB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: toB64url(r.clientDataJSON),
      attestationObject: toB64url(r.attestationObject),
      transports: r.getTransports?.() ?? [],
    },
    clientExtensionResults: cred.getClientExtensionResults(),
    authenticatorAttachment: cred.authenticatorAttachment ?? undefined,
  };
}

export async function getPasskey(options: Json): Promise<Json> {
  const publicKey: PublicKeyCredentialRequestOptions = {
    ...options,
    challenge: fromB64url(options.challenge),
    allowCredentials: descriptors(options.allowCredentials),
  } as PublicKeyCredentialRequestOptions;
  const cred = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential | null;
  if (!cred) throw new Error("No passkey was chosen.");
  const r = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id,
    rawId: toB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: toB64url(r.clientDataJSON),
      authenticatorData: toB64url(r.authenticatorData),
      signature: toB64url(r.signature),
      userHandle: toB64url(r.userHandle),
    },
    clientExtensionResults: cred.getClientExtensionResults(),
    authenticatorAttachment: cred.authenticatorAttachment ?? undefined,
  };
}

/** Turn browser WebAuthn errors into something a person can act on. */
export function passkeyMessage(err: unknown): string {
  if (err instanceof DOMException) {
    if (err.name === "NotAllowedError")
      return "No pal passkey was used. If this device isn't paired yet, tap “Pair it with a code” below.";
    if (err.name === "InvalidStateError") return "This device already has a pal passkey. Sign in with it instead.";
    if (err.name === "SecurityError") return "Passkeys need the https tailnet address (or localhost on the PC).";
  }
  return err instanceof Error ? err.message : "Passkey failed.";
}
