"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Icon from "@/components/Icon";
import ThemeToggle from "@/components/ThemeToggle";
import { api, type AuthStatus } from "@/lib/api";
import { cancelled, passkeysSupported, signWithPasskey } from "@/lib/passkey";

const MIN_PASSWORD = 8;

export default function LoginPage() {
  const router = useRouter();
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);

  const next = () => {
    const target = new URLSearchParams(window.location.search).get("next");
    return target && target.startsWith("/") && !target.startsWith("//") && !target.startsWith("/login") ? target : "/";
  };

  useEffect(() => {
    api.authStatus()
      .then((s) => {
        if (s.user) { router.replace(next()); return; }
        setStatus(s);
        if (s.suggested_name) setName(s.suggested_name);
      })
      .catch((e) => setError(`Can't reach the vault server: ${e instanceof Error ? e.message : String(e)}`));
  }, [router]);

  const setup = status?.setup_needed ?? false;
  const canPasskey = !setup && !!status?.passkeys_available && passkeysSupported();

  const withPasskey = async () => {
    setBusy(true);
    setError(null);
    try {
      const start = await api.passkeyLoginOptions();
      const credential = await signWithPasskey(start.options);
      await api.passkeyLogin(start.ceremony, credential);
      router.replace(next());
    } catch (err) {
      if (!cancelled(err)) setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  const mismatch = setup && confirm.length > 0 && confirm !== password;
  const tooShort = setup && password.length > 0 && password.length < MIN_PASSWORD;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (setup && (password !== confirm || password.length < MIN_PASSWORD)) return;
    setBusy(true);
    setError(null);
    try {
      await (setup ? api.setup(name, password) : api.login(name, password));
      router.replace(next());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPassword("");
      setConfirm("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login-wrap">
      <div style={{ position: "fixed", top: 14, right: 16 }}><ThemeToggle /></div>
      <div className="card login-card">
        <span className="logo"><Icon name="file" size={24} /></span>
        <h1>{setup ? "Set up your vault" : "Document Vault"}</h1>
        <p className="muted" style={{ margin: 0 }}>
          {setup
            ? "Choose the password you'll sign in with. You'll be the admin and can add family members later."
            : "Your family's papers, answered privately on this computer."}
        </p>
        {error && <div className="notice bad" role="alert" style={{ marginTop: 20, textAlign: "left" }}><Icon name="warn" />{error}</div>}
        {!status && !error && <p className="muted" style={{ margin: "28px 0" }}><span className="spinner" /></p>}
        {canPasskey && (
          <>
            <button className="btn primary passkey-btn" type="button" onClick={withPasskey} disabled={busy} autoFocus>
              <Icon name="fingerprint" size={18} />Sign in with Touch ID or fingerprint
            </button>
            <div className="or"><span>or use your password</span></div>
          </>
        )}
        {status && !setup && !status.passkeys_available && status.passkey_hint && (
          <p className="fine" style={{ marginTop: 16 }}>
            <Icon name="fingerprint" size={11} /> Want Touch ID? <a href={`http://localhost:${window.location.port || "80"}/login/`}>Open the vault as localhost</a>.
          </p>
        )}
        {status && (
          <form className={`login-form${canPasskey ? " after-passkey" : ""}`} onSubmit={submit}>
            <label>
              <span>Your name</span>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={60}
                     autoComplete="username" autoCapitalize="none" required autoFocus={!name && !canPasskey} />
            </label>
            <label>
              <span>{setup ? "New password" : "Password"}</span>
              <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                     autoComplete={setup ? "new-password" : "current-password"} required autoFocus={!!name && !canPasskey}
                     aria-invalid={tooShort || undefined} />
              {tooShort && <small className="field-note">At least {MIN_PASSWORD} characters.</small>}
            </label>
            {setup && (
              <label>
                <span>Type it again</span>
                <input className="input" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)}
                       autoComplete="new-password" required aria-invalid={mismatch || undefined} />
                {mismatch && <small className="field-note">The two passwords don&apos;t match.</small>}
              </label>
            )}
            <button className="btn primary" type="submit"
                    disabled={busy || !name.trim() || !password || (setup && (password !== confirm || password.length < MIN_PASSWORD))}>
              {busy ? "Please wait…" : setup ? "Create account" : "Sign in"}
            </button>
          </form>
        )}
        <p className="fine">
          <Icon name="lock" size={11} /> Documents never leave this computer. Each family member signs in with their own
          password or passkey and sees only their own documents.{!setup && status && " Forgot yours? Ask the admin to reset it in Settings."}
        </p>
      </div>
    </main>
  );
}
