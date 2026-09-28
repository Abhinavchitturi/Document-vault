"use client";

import { useEffect, useState } from "react";
import AppShell, { useVault } from "@/components/AppShell";
import Icon from "@/components/Icon";
import { useTheme, type Theme } from "@/components/ThemeToggle";
import { api, type Settings } from "@/lib/api";
import { startSession } from "@/lib/session";

const SEARCH_LABEL: Record<string, string> = {
  hybrid: "Hybrid: meaning + exact words and numbers",
  vector: "Meaning only (vector)",
  keyword: "Exact words only (keyword)",
};

function SettingsView() {
  const { refresh } = useVault();
  const [theme, setTheme] = useTheme();
  const [s, setS] = useState<Settings | null>(null);
  const [model, setModel] = useState("");
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const load = () => api.settings().then((r) => { setS(r); setModel(r.llm_model ?? ""); setName(r.owner); })
    .catch((e) => setMessage({ ok: false, text: e.message }));
  useEffect(() => { load(); }, []);

  const save = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const r = await api.saveSettings({ llm_model: model });
      setS(r);
      setMessage({ ok: true, text: `Answers now come from ${model}.` });
      refresh();
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };

  const saveName = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const r = await api.saveSettings({ owner_name: name });
      setS(r);
      setName(r.owner);
      startSession(r.owner);
      setMessage({ ok: true, text: `The vault now belongs to ${r.owner}. All your documents came along.` });
      refresh();
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page">
      <div className="settings">
        {message && (
          <div className={`notice ${message.ok ? "good" : "bad"}`} role="status"><Icon name={message.ok ? "ok" : "warn"} />{message.text}</div>
        )}

        <section className="card">
          <h2>You</h2>
          <div className="setting-row">
            <div className="label">
              <strong>Your name</strong>
              <span>Shown on the sign-in screen and as the owner of your documents.</span>
            </div>
            <form style={{ display: "flex", gap: 8 }} onSubmit={(e) => { e.preventDefault(); saveName(); }}>
              <input className="input" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} aria-label="Your name" />
              <button className="btn primary" type="submit" disabled={saving || !name.trim() || name.trim() === s?.owner}>Save</button>
            </form>
          </div>
        </section>

        <section className="card">
          <h2>Assistant</h2>
          <div className="setting-row">
            <div className="label">
              <strong>Answer model</strong>
              <span>Runs on this computer with Ollama. Download more with <code>ollama pull &lt;name&gt;</code>.</span>
            </div>
            {s && s.llm_models.length > 0 ? (
              <div style={{ display: "flex", gap: 8 }}>
                <select className="input" value={model} onChange={(e) => setModel(e.target.value)} aria-label="Answer model">
                  {!s.llm_models.includes(model) && model && <option value={model}>{model} (not downloaded)</option>}
                  {s.llm_models.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
                <button className="btn primary" type="button" onClick={save} disabled={saving || model === s.llm_model}>
                  {saving ? "Saving…" : "Use"}
                </button>
              </div>
            ) : (
              <span className="muted">{s ? s.llm_model ?? "Off" : "…"}</span>
            )}
          </div>
          <div className="setting-row">
            <div className="label"><strong>Status</strong><span>{s?.llm_status.message ?? "Checking…"}</span></div>
            <span className={`pill ${s?.llm_status.ready ? "ready" : "not-ready"}`}><span className="dot" />{s?.llm_status.ready ? "Ready" : "Offline"}</span>
          </div>
        </section>

        <section className="card">
          <h2>Appearance</h2>
          <div className="setting-row">
            <div className="label"><strong>Theme</strong><span>Remembered in this browser.</span></div>
            <div className="segmented" role="group" aria-label="Theme">
              {(["system", "light", "dark"] as Theme[]).map((t) => (
                <button key={t} type="button" aria-pressed={theme === t} onClick={() => setTheme(t)}>
                  {t === "system" ? "Match computer" : t[0].toUpperCase() + t.slice(1)}
                </button>
              ))}
            </div>
          </div>
        </section>

        <section className="card">
          <h2>About this vault</h2>
          {s ? (
            <dl className="kv">
              <dt>Owner</dt><dd style={{ textTransform: "capitalize" }}>{s.owner}</dd>
              <dt>Search</dt><dd>{SEARCH_LABEL[s.search_method] ?? s.search_method}</dd>
              <dt>Search model</dt><dd>{s.embedding_model}</dd>
              <dt>Document records</dt><dd><code>{s.database}</code></dd>
              <dt>Files and vectors</dt><dd><code>{s.index_dir}</code></dd>
              <dt>Photos and scans</dt><dd>{s.ocr_available ? "Supported (Tesseract)" : <>Not yet: <code>brew install tesseract</code></>}</dd>
            </dl>
          ) : <p className="muted">Loading…</p>}
          <p className="faint" style={{ fontSize: 13, marginBottom: 0 }}>
            <Icon name="lock" size={11} /> Everything stays on this computer: no document text is sent anywhere.
          </p>
        </section>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  return <AppShell title="Settings"><SettingsView /></AppShell>;
}
