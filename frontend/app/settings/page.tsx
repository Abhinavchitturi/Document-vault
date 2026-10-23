"use client";

import { useEffect, useState } from "react";
import AppShell, { useVault } from "@/components/AppShell";
import Icon from "@/components/Icon";
import { useTheme, type Theme } from "@/components/ThemeToggle";
import { api, formatDate, type ActivityEntry, type BackupSchedule, type Device, type Member, type Pairing, type Passkey, type PhoneAccess, type ReminderSettings, type Settings } from "@/lib/api";
import { cancelled, createPasskey, deviceName, passkeysSupported } from "@/lib/passkey";

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
      setMessage({ ok: true, text: `You're now ${r.owner}: sign in with this name from now on. All your documents came along.` });
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
              <span>The name you sign in with, and the owner of your documents.</span>
            </div>
            <form style={{ display: "flex", gap: 8 }} onSubmit={(e) => { e.preventDefault(); saveName(); }}>
              <input className="input" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} aria-label="Your name" />
              <button className="btn primary" type="submit" disabled={saving || !name.trim() || name.trim() === s?.owner}>Save</button>
            </form>
          </div>
          <Passkeys />
          <PasswordChange onDone={(text) => setMessage({ ok: true, text })} />
        </section>

        <Reminders />

        <Phones />

        {s?.is_admin && <FamilyMembers familyMode={s.family_mode} onFamilyMode={async (on) => {
          try { setS(await api.saveSettings({ family_mode: on })); refresh(); }
          catch (e) { setMessage({ ok: false, text: (e as Error).message }); }
        }} />}

        <Security settings={s} />

        <Activity isAdmin={!!s?.is_admin} />

        <section className="card">
          <h2>Assistant</h2>
          <div className="setting-row">
            <div className="label">
              <strong>Answer model</strong>
              <span>Runs on this computer with Ollama. Download more with <code>ollama pull &lt;name&gt;</code>.</span>
            </div>
            {s && s.is_admin && s.llm_models.length > 0 ? (
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
              <dt>Reading dates and amounts</dt>
              <dd>{s.facts_reader === "needle" ? "Needle 3 (on this computer)" : s.facts_reader === "ollama"
                ? <>The answer model. For Needle 3: <code>pip install cactus-needle</code>, then <code>python -m backend.extract.needle_setup</code></>
                : <>Text patterns. For Needle 3: <code>pip install cactus-needle</code>, then <code>python -m backend.extract.needle_setup</code></>}</dd>
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

function Passkeys() {
  const [keys, setKeys] = useState<Passkey[] | null>(null);
  const [available, setAvailable] = useState<{ ok: boolean; hint: string | null }>({ ok: true, hint: null });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const load = () => api.passkeys().then(setKeys).catch((e) => setMessage({ ok: false, text: e.message }));
  useEffect(() => {
    load();
    api.authStatus().then((s) => setAvailable({ ok: s.passkeys_available && passkeysSupported(), hint: s.passkey_hint }));
  }, []);

  const add = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const start = await api.passkeyAddOptions();
      const credential = await createPasskey(start.options);
      const added = await api.addPasskey(start.ceremony, credential, deviceName());
      setMessage({ ok: true, text: `Passkey added for ${added.name}. Next time, sign in with Touch ID or your fingerprint.` });
      load();
    } catch (e) {
      if (cancelled(e)) setMessage(null);
      else setMessage({ ok: false, text: e instanceof DOMException && e.name === "InvalidStateError"
        ? "This device already has a passkey for your account." : (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (k: Passkey) => {
    if (!window.confirm(`Remove the passkey "${k.name}"? You can still sign in with your password.`)) return;
    try {
      await api.removePasskey(k.id);
      setMessage({ ok: true, text: `Removed "${k.name}". Also delete it from the device's saved passwords if you like.` });
      load();
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    }
  };

  return (
    <div className="setting-row">
      <div className="label">
        <strong>Passkeys</strong>
        <span>Sign in with Touch ID, Face ID or your phone&apos;s fingerprint instead of typing your password.
          Your fingerprint never leaves the device.</span>
      </div>
      <div className="stack-form">
        {message && <div className={`notice ${message.ok ? "good" : "bad"}`} role="status"><Icon name={message.ok ? "ok" : "warn"} />{message.text}</div>}
        {keys && keys.length > 0 && (
          <ul className="passkey-list">
            {keys.map((k) => (
              <li key={k.id}>
                <Icon name="fingerprint" size={18} />
                <div className="meta">
                  <strong>{k.name}</strong>
                  <span>Added {formatDate(k.created_at)}{k.last_used_at ? ` · last used ${formatDate(k.last_used_at)}` : " · not used yet"}</span>
                </div>
                <button className="icon-btn danger" type="button" aria-label={`Remove passkey ${k.name}`} title="Remove" onClick={() => remove(k)}>
                  <Icon name="trash" />
                </button>
              </li>
            ))}
          </ul>
        )}
        {available.ok ? (
          <div className="row">
            <button className="btn primary" type="button" onClick={add} disabled={busy}>
              <Icon name="fingerprint" size={15} />{busy ? "Waiting for Touch ID…" : "Add a passkey"}
            </button>
          </div>
        ) : (
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            {available.hint ?? "This browser doesn't support passkeys."}{" "}
            {available.hint && <a href={`http://localhost:${window.location.port || "80"}/settings/`}>Switch to localhost</a>}
          </p>
        )}
      </div>
    </div>
  );
}

const EXPIRY_CHOICES = [60, 30, 14, 7, 3, 1, 0];
const DUE_CHOICES = [14, 7, 3, 1, 0];
const dayLabel = (d: number) => (d === 0 ? "On the day" : d === 1 ? "1 day before" : `${d} days before`);

function Reminders() {
  const [s, setS] = useState<ReminderSettings | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => { api.reminders().then((r) => setS(r.settings)).catch((e) => setMessage({ ok: false, text: e.message })); }, []);

  const save = async (changes: Partial<ReminderSettings>) => {
    try {
      setS(await api.saveReminderSettings(changes));
      setMessage({ ok: true, text: "Saved. Phones pick up the change the next time the app connects." });
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    }
  };
  const toggle = (list: number[], d: number) => (list.includes(d) ? list.filter((x) => x !== d) : [...list, d]);

  return (
    <section className="card" id="reminders">
      <h2>Reminders</h2>
      <p className="muted" style={{ marginTop: 0, fontSize: 14 }}>
        Before a policy or warranty expires and before a bill is due. They appear on your phone (even when this Mac is off),
        on this Mac while the vault runs, and on the Dashboard. Mark one <strong>Paid</strong> or <strong>Done</strong> to stop it.
      </p>
      {message && <div className={`notice ${message.ok ? "good" : "bad"}`} role="status" style={{ marginBottom: 12 }}><Icon name={message.ok ? "ok" : "warn"} />{message.text}</div>}
      {s && (
        <>
          <div className="setting-row">
            <div className="label"><strong>Policies and warranties</strong><span>When to remind you before they expire.</span></div>
            <div className="day-chips" role="group" aria-label="Expiry reminders">
              {EXPIRY_CHOICES.map((d) => (
                <button key={d} type="button" className="chip" aria-pressed={s.expiry_days.includes(d)}
                        onClick={() => save({ expiry_days: toggle(s.expiry_days, d) })}>{dayLabel(d)}</button>
              ))}
            </div>
          </div>
          <div className="setting-row">
            <div className="label"><strong>Bills</strong><span>When to remind you before a payment is due.</span></div>
            <div className="day-chips" role="group" aria-label="Bill reminders">
              {DUE_CHOICES.map((d) => (
                <button key={d} type="button" className="chip" aria-pressed={s.due_days.includes(d)}
                        onClick={() => save({ due_days: toggle(s.due_days, d) })}>{dayLabel(d)}</button>
              ))}
            </div>
          </div>
          <div className="setting-row">
            <div className="label"><strong>Time of day</strong><span>When phone reminders appear.</span></div>
            <input className="input" type="time" value={s.time_of_day} aria-label="Reminder time"
                   onChange={(e) => e.target.value && save({ time_of_day: e.target.value })} />
          </div>
          <div className="setting-row">
            <div className="label"><strong>Show on this Mac</strong><span>Desktop notifications while the vault is running, including ones missed while it was off.</span></div>
            <label className="check"><input type="checkbox" checked={s.desktop} onChange={(e) => save({ desktop: e.target.checked })} /> On</label>
          </div>
          <div className="setting-row">
            <div className="label"><strong>Calendar</strong><span>Add every expiry and due date to Apple, Google or Outlook calendar, with these reminders. Download again after adding documents.</span></div>
            <a className="btn" href="/api/reminders/calendar.ics" download><Icon name="open" size={14} />Download calendar file</a>
          </div>
        </>
      )}
    </section>
  );
}

function Phones() {
  const [state, setState] = useState<PhoneAccess | null>(null);
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [left, setLeft] = useState(0);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const load = () => api.devices().then(setState).catch((e) => setMessage({ ok: false, text: e.message }));
  useEffect(() => { load(); }, []);

  // While the QR code is up: count down, and notice when the phone has paired.
  useEffect(() => {
    if (!pairing) return;
    const started = Date.now();
    const before = state?.devices.length ?? 0;
    const tick = setInterval(() => {
      const remaining = pairing.expires_in - Math.floor((Date.now() - started) / 1000);
      setLeft(remaining);
      if (remaining <= 0) { setPairing(null); clearInterval(tick); }
    }, 1000);
    const poll = setInterval(() => {
      api.devices().then((d) => {
        setState(d);
        if (d.devices.length > before) {
          setPairing(null);
          setMessage({ ok: true, text: `Paired ${d.devices[d.devices.length - 1].name}.` });
        }
      }).catch(() => {});
    }, 2500);
    return () => { clearInterval(tick); clearInterval(poll); };
  }, [pairing]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setMessage(null);
    try {
      const p = await api.startPairing();
      setLeft(p.expires_in);
      setPairing(p);
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    }
  };

  const remove = async (d: Device) => {
    if (!window.confirm(`Remove ${d.name}? It will need to be paired again.`)) return;
    await api.removeDevice(d.id).catch((e) => setMessage({ ok: false, text: e.message }));
    load();
  };

  const qrSrc = pairing ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(pairing.qr_svg)}` : "";
  return (
    <section className="card">
      <h2>Phones</h2>
      <p className="muted" style={{ marginTop: 0, fontSize: 14 }}>
        Use the Document Vault app on your phone: chat, browse, and scan bills with the camera. Phones reach this Mac on the
        same Wi-Fi, or from anywhere through Tailscale, while the vault is running here.
      </p>
      {message && <div className={`notice ${message.ok ? "good" : "bad"}`} role="status" style={{ marginBottom: 12 }}><Icon name={message.ok ? "ok" : "warn"} />{message.text}</div>}
      {state && !state.enabled && (
        <div className="notice"><Icon name="warn" /><span>Phone access is off. Stop the vault and start it with <code>python -m backend.api.app --phones</code>.</span></div>
      )}
      {state?.enabled && (
        <dl className="kv" style={{ marginBottom: 12 }}>
          <dt>At home</dt><dd>{state.urls.find((u) => !u.startsWith("https://100.")) ? "On this Wi-Fi" : "Not on a Wi-Fi network"}</dd>
          <dt>Away from home</dt>
          <dd>{state.tunnel ? "On, through Tailscale" : <>Off: install Tailscale on this Mac and the phones (see README → Phone app)</>}</dd>
        </dl>
      )}
      {state?.devices.length ? (
        <ul className="passkey-list">
          {state.devices.map((d) => (
            <li key={d.id}>
              <Icon name="phone" size={18} />
              <div className="meta">
                <strong>{d.name}</strong>
                <span>Paired {formatDate(d.created_at)}{d.last_seen_at ? ` · last used ${formatDate(d.last_seen_at)}` : ""}</span>
              </div>
              <button className="icon-btn danger" type="button" aria-label={`Remove ${d.name}`} title="Remove" onClick={() => remove(d)}><Icon name="trash" /></button>
            </li>
          ))}
        </ul>
      ) : null}
      {state?.enabled && !pairing && (
        <button className="btn primary" type="button" onClick={start}><Icon name="phone" size={15} />Pair a phone</button>
      )}
      {pairing && (
        <div className="pairing">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={qrSrc} alt="Pairing QR code" width={220} height={220} />
          <div>
            <ol>
              <li>Open the <strong>Document Vault</strong> app on your phone.</li>
              <li>Tap <strong>Scan pairing code</strong> and point the camera at this code.</li>
              <li>That&apos;s it: the phone signs in as you.</li>
            </ol>
            <p className="faint" style={{ fontSize: 12.5 }}>
              Works once, for {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")} more. Only show it to your own phone.
              The code also locks the app to this vault&apos;s certificate ({pairing.fingerprint.slice(0, 8)}…).
            </p>
            <button className="btn small" type="button" onClick={() => setPairing(null)}>Cancel</button>
          </div>
        </div>
      )}
    </section>
  );
}

function PasswordChange({ onDone }: { onDone: (message: string) => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mismatch = again.length > 0 && again !== next;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.changePassword(current, next);
      setCurrent(""); setNext(""); setAgain("");
      onDone("Password changed. You've been signed out on your other devices.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="setting-row">
      <div className="label">
        <strong>Password</strong>
        <span>Changing it signs you out everywhere else.</span>
      </div>
      <form className="stack-form" onSubmit={submit}>
        {error && <div className="notice bad" role="alert"><Icon name="warn" />{error}</div>}
        <label><span>Current password</span>
          <input className="input" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required /></label>
        <label><span>New password (8 or more characters)</span>
          <input className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={8} /></label>
        <label><span>Type it again</span>
          <input className="input" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required aria-invalid={mismatch || undefined} />
          {mismatch && <small className="field-note">The two passwords don&apos;t match.</small>}</label>
        <div className="row">
          <button className="btn primary" type="submit" disabled={busy || !current || next.length < 8 || next !== again}>
            {busy ? "Saving…" : "Change password"}
          </button>
        </div>
      </form>
    </div>
  );
}

function FamilyMembers({ familyMode, onFamilyMode }: { familyMode: boolean; onFamilyMode: (on: boolean) => void }) {
  const [members, setMembers] = useState<Member[] | null>(null);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [admin, setAdmin] = useState(false);
  const [resetting, setResetting] = useState<number | null>(null);
  const [resetTo, setResetTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const load = () => api.members().then(setMembers).catch((e) => setMessage({ ok: false, text: e.message }));
  useEffect(() => { load(); }, []);

  const run = async (action: () => Promise<string>) => {
    setBusy(true);
    setMessage(null);
    try {
      setMessage({ ok: true, text: await action() });
      load();
    } catch (err) {
      setMessage({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    run(async () => {
      const m = await api.addMember(name, password, admin);
      setName(""); setPassword(""); setAdmin(false);
      return `Added ${m.name}. Tell them their password; they can change it in Settings.`;
    });
  };

  const reset = (e: React.FormEvent, m: Member) => {
    e.preventDefault();
    run(async () => {
      await api.resetPassword(m.id, resetTo);
      setResetting(null); setResetTo("");
      return `New password set for ${m.name}. They've been signed out everywhere.`;
    });
  };

  return (
    <section className="card">
      <h2>Family members</h2>
      <div className="setting-row">
        <div className="label">
          <strong>Who sees which documents</strong>
          <span>{familyMode
            ? "Everyone sees every document, except ones their owner marked private. Only the owner can change or delete a document."
            : "Each person sees their own documents, and ones others shared with them."}</span>
        </div>
        <div className="segmented" role="group" aria-label="Who sees which documents">
          <button type="button" aria-pressed={familyMode} onClick={() => onFamilyMode(true)}>Whole family</button>
          <button type="button" aria-pressed={!familyMode} onClick={() => onFamilyMode(false)}>Each their own</button>
        </div>
      </div>
      <p className="muted" style={{ fontSize: 14 }}>
        Each person signs in with their own password.
      </p>
      {message && <div className={`notice ${message.ok ? "good" : "bad"}`} role="status" style={{ marginBottom: 12 }}><Icon name={message.ok ? "ok" : "warn"} />{message.text}</div>}
      <ul className="members">
        {members?.map((m) => (
          <li key={m.id}>
            <span className="avatar-initial">{m.name.slice(0, 1)}</span>
            <div className="who">
              <strong>{m.name}</strong>
              <span>{m.is_admin ? "Admin" : "Family member"}{m.can_sign_in ? "" : " · no password yet"}</span>
            </div>
            {resetting === m.id ? (
              <form onSubmit={(e) => reset(e, m)}>
                <input className="input" type="password" autoComplete="new-password" placeholder="New password" aria-label={`New password for ${m.name}`}
                       value={resetTo} onChange={(e) => setResetTo(e.target.value)} minLength={8} required autoFocus />
                <button className="btn primary small" type="submit" disabled={busy || resetTo.length < 8}>Set</button>
                <button className="btn small" type="button" onClick={() => { setResetting(null); setResetTo(""); }}>Cancel</button>
              </form>
            ) : (
              <button className="btn small" type="button" onClick={() => { setResetting(m.id); setResetTo(""); }}>
                {m.can_sign_in ? "Reset password" : "Set password"}
              </button>
            )}
          </li>
        ))}
      </ul>
      <form className="stack-form" onSubmit={add} style={{ marginTop: 16 }}>
        <strong style={{ fontSize: 14 }}>Add a family member</strong>
        <label><span>Name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} autoComplete="off" required /></label>
        <label><span>Password (8 or more characters)</span>
          <input className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} required /></label>
        <label className="check"><input type="checkbox" checked={admin} onChange={(e) => setAdmin(e.target.checked)} />
          Admin (can add members and reset passwords)</label>
        <div className="row"><button className="btn primary" type="submit" disabled={busy || !name.trim() || password.length < 8}>
          <Icon name="plus" size={14} />Add member</button></div>
      </form>
    </section>
  );
}

function AutoBackup() {
  const [info, setInfo] = useState<BackupSchedule | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [folder, setFolder] = useState("");
  const [frequency, setFrequency] = useState("daily");
  const [keep, setKeep] = useState(7);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const show = (r: BackupSchedule) => {
    setInfo(r);
    setEnabled(r.enabled);
    setFolder(r.folder);
    setFrequency(r.frequency);
    setKeep(r.keep);
  };
  useEffect(() => {
    api.backupSchedule().then(show).catch((e) => setMessage({ ok: false, text: e.message }));
  }, []);

  const when = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "never";

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      show(await api.saveBackupSchedule({ enabled, folder, frequency, keep, password: password || undefined }));
      setPassword("");
      setMessage({ ok: true, text: enabled ? "Saved. Backups will be made automatically." : "Automatic backups are off." });
    } catch (err) {
      setMessage({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const runNow = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const r = await api.runBackupNow();
      show(r);
      setMessage(r.last_error ? { ok: false, text: r.last_error } : { ok: true, text: `Backed up: ${r.last_file}` });
    } catch (err) {
      setMessage({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="stack-form" onSubmit={save} style={{ marginTop: 20 }}>
      <strong style={{ fontSize: 14 }}>Automatic backups</strong>
      <span className="muted" style={{ fontSize: 14 }}>
        Saves an encrypted backup by itself to a folder you choose: a second disk, a USB drive, or a NAS share
        that is connected to this Mac. The folder only ever holds encrypted files. The vault itself stays on this Mac.
      </span>
      <label className="check"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Back up automatically</label>
      {enabled && (
        <>
          <label><span>Folder (full path, for example /Volumes/NAS/Backups)</span>
            <input className="input" value={folder} onChange={(e) => setFolder(e.target.value)} autoComplete="off" spellCheck={false} required /></label>
          <label><span>How often</span>
            <select className="input" value={frequency} onChange={(e) => setFrequency(e.target.value)}>
              <option value="daily">Every day</option>
              <option value="weekly">Every week</option>
            </select></label>
          <label><span>Backups to keep (older ones are deleted)</span>
            <input className="input" type="number" min={1} max={100} value={keep} onChange={(e) => setKeep(Number(e.target.value) || 1)} /></label>
          <label><span>{info?.has_password ? "Backup password (leave empty to keep the saved one)" : "Backup password (10 or more characters)"}</span>
            <input className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)}
                   minLength={10} required={!info?.has_password} /></label>
          <span className="muted" style={{ fontSize: 13 }}>
            The password is saved on this Mac next to the vault key, so backups can run unattended. Keep a copy somewhere
            else too: without it a backup can&apos;t be opened.
          </span>
        </>
      )}
      {info?.enabled && (
        <div className="muted" style={{ fontSize: 14 }}>
          Last successful backup: {when(info.last_success)}{info.last_file ? ` (${info.last_file})` : ""}
          {info.last_success ? "" : ". The first one runs within a few minutes of saving."}
        </div>
      )}
      {info?.last_error && <div className="notice bad" role="status"><Icon name="warn" />Last backup failed: {info.last_error}</div>}
      {message && <div className={`notice ${message.ok ? "good" : "bad"}`} role="status"><Icon name={message.ok ? "ok" : "warn"} />{message.text}</div>}
      <div className="row">
        <button className="btn primary" type="submit" disabled={busy}>{busy ? "Working…" : "Save"}</button>
        {info?.enabled && <button className="btn" type="button" onClick={runNow} disabled={busy}>Back up now</button>}
      </div>
    </form>
  );
}

function Security({ settings }: { settings: Settings | null }) {
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const backup = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const name = await api.downloadBackup(password);
      setPassword(""); setAgain("");
      setMessage({ ok: true, text: `Saved ${name}. Copy it to a USB drive or cloud storage; keep the backup password somewhere else.` });
    } catch (err) {
      setMessage({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card">
      <h2>Security</h2>
      <div className="setting-row">
        <div className="label">
          <strong>Encryption</strong>
          <span>Documents, scans, the search index, file names and ID numbers are stored encrypted (AES-256).
            The key is kept in {settings?.key_kept_in ?? "…"}, not in the vault folder.</span>
        </div>
        <span className="pill ready"><Icon name="lock" size={11} />On</span>
      </div>
      <div className="setting-row">
        <div className="label">
          <strong>Recovery key</strong>
          <span>For a new Mac, or if the Keychain is lost. Show it once in Terminal, write it on paper:
            <code> python -m backend.security.cli recovery-key</code></span>
        </div>
      </div>
      {settings?.is_admin && (
        <form className="stack-form" onSubmit={backup} style={{ marginTop: 12 }}>
          <strong style={{ fontSize: 14 }}>Encrypted backup</strong>
          <span className="muted" style={{ fontSize: 14 }}>
            One file with everyone&apos;s documents, accounts and reminders. It opens only with the backup password,
            so it&apos;s safe on a USB drive or in the cloud. If you forget the password, the backup can&apos;t be opened.
          </span>
          <label><span>Backup password (10 or more characters)</span>
            <input className="input" type="password" autoComplete="new-password" value={password}
                   onChange={(e) => setPassword(e.target.value)} minLength={10} required /></label>
          <label><span>Type it again</span>
            <input className="input" type="password" autoComplete="new-password" value={again}
                   onChange={(e) => setAgain(e.target.value)} required /></label>
          {message && <div className={`notice ${message.ok ? "good" : "bad"}`} role="status"><Icon name={message.ok ? "ok" : "warn"} />{message.text}</div>}
          <div className="row"><button className="btn primary" type="submit" disabled={busy || password.length < 10 || password !== again}>
            {busy ? "Making the backup…" : "Download backup"}</button></div>
        </form>
      )}
      {settings?.is_admin && <AutoBackup />}
    </section>
  );
}

function Activity({ isAdmin }: { isAdmin: boolean }) {
  const [everyone, setEveryone] = useState(false);
  const [entries, setEntries] = useState<ActivityEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.activity(everyone).then((r) => setEntries(r.entries)).catch((e) => setError(e.message));
  }, [everyone]);

  const when = (iso: string) => new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

  return (
    <section className="card">
      <h2>Activity</h2>
      <div className="setting-row">
        <div className="label">
          <span>Sign-ins and what was opened, added or removed{everyone ? " by everyone" : " with your account"}.
            Anything you don&apos;t recognise? Change your password and remove unknown phones.</span>
        </div>
        {isAdmin && (
          <div className="segmented" role="group" aria-label="Whose activity">
            <button type="button" aria-pressed={!everyone} onClick={() => setEveryone(false)}>Mine</button>
            <button type="button" aria-pressed={everyone} onClick={() => setEveryone(true)}>Everyone</button>
          </div>
        )}
      </div>
      {error && <div className="notice bad" role="status"><Icon name="warn" />{error}</div>}
      {entries && entries.length === 0 && <p className="muted">Nothing yet.</p>}
      {entries && entries.length > 0 && (
        <ul className="activity">
          {entries.slice(0, 100).map((e) => (
            <li key={e.id} className={e.ok ? "" : "failed"}>
              <time dateTime={e.at}>{when(e.at)}</time>
              <span className="who">{everyone ? <strong>{e.user_name || "?"} </strong> : null}{e.what}{e.detail ? `: ${e.detail}` : ""}</span>
              <span className="via faint">{e.via === "web" ? "browser" : e.via}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function SettingsPage() {
  return <AppShell title="Settings"><SettingsView /></AppShell>;
}
