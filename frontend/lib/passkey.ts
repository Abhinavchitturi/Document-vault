// Passkeys in the browser: Touch ID / Face ID / fingerprint via WebAuthn (navigator.credentials).
// The server (backend/auth/passkeys.py) sends options as JSON with base64url strings; the browser
// API wants ArrayBuffers, and its answer goes back as JSON. No library: the vault loads nothing
// from the internet.

type Json = Record<string, unknown>;

const fromB64url = (s: string): ArrayBuffer => {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
};

const toB64url = (buf: ArrayBuffer | null): string | null => {
  if (!buf) return null;
  let bin = "";
  new Uint8Array(buf).forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};

/** Can this browser use passkeys at all (secure context + WebAuthn)? */
export const passkeysSupported = () =>
  typeof window !== "undefined" && window.isSecureContext && typeof window.PublicKeyCredential === "function";

/** What to call the passkey in Settings, from the device it was made on. */
export function deviceName(): string {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) return "Android phone";
  if (/Macintosh/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows PC";
  return "This device";
}

const descriptors = (list: unknown) =>
  ((list as Json[] | undefined) ?? []).map((c) => ({ ...c, id: fromB64url(c.id as string) })) as PublicKeyCredentialDescriptor[];

export async function createPasskey(optionsJson: string): Promise<Json> {
  const o = JSON.parse(optionsJson);
  const cred = (await navigator.credentials.create({
    publicKey: {
      ...o,
      challenge: fromB64url(o.challenge),
      user: { ...o.user, id: fromB64url(o.user.id) },
      excludeCredentials: descriptors(o.excludeCredentials),
    },
  })) as PublicKeyCredential;
  const r = cred.response as AuthenticatorAttestationResponse;
  return {
    id: cred.id,
    rawId: toB64url(cred.rawId),
    type: cred.type,
    clientExtensionResults: cred.getClientExtensionResults(),
    authenticatorAttachment: cred.authenticatorAttachment,
    response: {
      clientDataJSON: toB64url(r.clientDataJSON),
      attestationObject: toB64url(r.attestationObject),
      transports: typeof r.getTransports === "function" ? r.getTransports() : [],
    },
  };
}

export async function signWithPasskey(optionsJson: string): Promise<Json> {
  const o = JSON.parse(optionsJson);
  const cred = (await navigator.credentials.get({
    publicKey: { ...o, challenge: fromB64url(o.challenge), allowCredentials: descriptors(o.allowCredentials) },
  })) as PublicKeyCredential;
  const r = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id,
    rawId: toB64url(cred.rawId),
    type: cred.type,
    clientExtensionResults: cred.getClientExtensionResults(),
    authenticatorAttachment: cred.authenticatorAttachment,
    response: {
      clientDataJSON: toB64url(r.clientDataJSON),
      authenticatorData: toB64url(r.authenticatorData),
      signature: toB64url(r.signature),
      userHandle: toB64url(r.userHandle),
    },
  };
}

/** A cancelled Touch ID prompt isn't an error worth shouting about. */
export const cancelled = (e: unknown) => e instanceof DOMException && (e.name === "NotAllowedError" || e.name === "AbortError");
