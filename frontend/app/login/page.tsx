"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Icon from "@/components/Icon";
import ThemeToggle from "@/components/ThemeToggle";
import { api } from "@/lib/api";
import { getSession, startSession } from "@/lib/session";

export default function LoginPage() {
  const router = useRouter();
  const [owner, setOwner] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);

  const next = () => {
    const target = new URLSearchParams(window.location.search).get("next");
    return target && target.startsWith("/") && !target.startsWith("//") ? target : "/";  // only paths on this site
  };

  useEffect(() => {
    if (getSession()) { router.replace(next()); return; }
    api.info().then((i) => setOwner(i.owner))
      .catch((e) => setError(`Can't reach the vault server: ${e instanceof Error ? e.message : String(e)}`));
  }, [router]);

  const rename = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const s = await api.saveSettings({ owner_name: name });
      setOwner(s.owner);
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const go = () => {
    if (!owner) return;
    startSession(owner);
    router.replace(next());
  };

  return (
    <main className="login-wrap">
      <div style={{ position: "fixed", top: 14, right: 16 }}><ThemeToggle /></div>
      <div className="card login-card">
        <span className="logo"><Icon name="file" size={24} /></span>
        <h1>Document Vault</h1>
        <p className="muted" style={{ margin: 0 }}>Your family&apos;s papers, answered privately on this computer.</p>
        {error && <div className="notice bad" style={{ marginTop: 20, textAlign: "left" }}><Icon name="warn" />{error}</div>}
        {!owner && !error && <p className="muted" style={{ margin: "28px 0" }}><span className="spinner" /></p>}
        {owner && (
          <>
            {editing ? (
              <form className="person" onSubmit={rename} style={{ cursor: "default" }}>
                <span className="avatar-initial">{(name || owner).slice(0, 1)}</span>
                <input className="input" style={{ flex: 1, minWidth: 0 }} value={name} maxLength={60} autoFocus
                       aria-label="Your name" onChange={(e) => setName(e.target.value)} />
                <button className="btn primary small" type="submit" disabled={saving || !name.trim()}>Save</button>
              </form>
            ) : (
            <button className="person" type="button" onClick={go} autoFocus>
              <span className="avatar-initial">{owner.slice(0, 1)}</span>
              <span style={{ flex: 1 }}><strong>{owner}</strong><span className="muted" style={{ fontSize: 13 }}>Continue to your vault</span></span>
              <Icon name="next" />
            </button>
            )}
            <button type="button" className="link-btn" style={{ marginTop: 0 }}
                    onClick={() => { setName(editing ? "" : owner); setEditing(!editing); }}>
              {editing ? "Cancel" : `Not ${owner}? Change name`}
            </button>
            <p className="fine">
              <Icon name="lock" size={11} /> Documents never leave this computer. Password sign-in and a separate
              account for each family member arrive in Phase 6; until then anyone using this computer can open the vault.
            </p>
          </>
        )}
      </div>
    </main>
  );
}
