"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import AppShell, { useVault } from "@/components/AppShell";
import { displayName, FileIcon } from "@/components/DocBits";
import Icon from "@/components/Icon";
import { api, CATEGORIES, CATEGORY_LABEL, formatBytes, formatDate, type Reminder, type VaultDocument } from "@/lib/api";

const QUICK = ["When does my bike insurance expire?", "Find the washing machine warranty", "When is the electricity bill due?"];

function Dashboard() {
  const { info } = useVault();
  const router = useRouter();
  const [docs, setDocs] = useState<VaultDocument[] | null>(null);
  const [question, setQuestion] = useState("");
  const [reminders, setReminders] = useState<Reminder[] | null>(null);

  useEffect(() => {
    api.documents().then(setDocs).catch(() => setDocs([]));
    api.reminders().then((r) => setReminders(r.items)).catch(() => setReminders([]));
  }, []);

  const mark = async (r: Reminder, done: boolean) => {
    try { setReminders((await api.markReminder(r.doc_id, r.kind, done)).items); } catch { /* keep the list as it was */ }
  };

  const ask = (q: string) => { if (q.trim()) router.push(`/chat/?q=${encodeURIComponent(q.trim())}`); };
  const total = info?.num_documents ?? 0;
  const size = (docs ?? []).reduce((sum, d) => sum + d.size_bytes, 0);

  return (
    <div className="page">
      <section className="stats" aria-label="Documents by type">
        <Link href="/documents/" className="stat total">
          <div className="label">Documents</div>
          <div className="value">{info ? total : "–"}</div>
        </Link>
        {CATEGORIES.filter((c) => c !== "other" || (info?.categories.other ?? 0) > 0).map((c) => (
          <Link key={c} href={`/documents/?type=${c}`} className="stat">
            <div className="label"><span className="dotc" style={{ background: `var(--c-${c})` }} />{CATEGORY_LABEL[c]}</div>
            <div className="value">{info ? info.categories[c] : "–"}</div>
          </Link>
        ))}
      </section>

      <div className="dash-grid">
        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          <section className="card ask-card">
            <h2>Ask your vault</h2>
            <form onSubmit={(e) => { e.preventDefault(); ask(question); }}>
              <input className="input" placeholder="e.g. When does my car insurance expire?" value={question}
                     onChange={(e) => setQuestion(e.target.value)} aria-label="Question" />
              <button className="btn primary" type="submit" disabled={!question.trim()}>Ask</button>
            </form>
            <div className="quick">
              {QUICK.map((q) => <button key={q} type="button" className="chip" onClick={() => ask(q)}>{q}</button>)}
            </div>
          </section>

          <section className="card">
            <div style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
              <h2 style={{ margin: 0, flex: 1 }}>Reminders</h2>
              <Link href="/settings/#reminders" className="btn small">Reminder settings</Link>
            </div>
            {reminders === null && <p className="muted">Loading…</p>}
            {reminders?.length === 0 && (
              <p className="muted" style={{ margin: 0 }}>Nothing expires or falls due soon.</p>
            )}
            <div className="rows">
              {reminders?.map((r) => (
                <div key={`${r.doc_id}-${r.kind}`} className={`row upcoming ${r.status}`}>
                  <FileIcon category={r.category} size={32} />
                  <div className="info">
                    <div className="name">{r.title}</div>
                    <div className="meta">
                      {r.body}
                      {r.next_notice && r.next_notice !== r.date && ` · next reminder ${formatDate(r.next_notice)}`}
                    </div>
                  </div>
                  {r.done ? (
                    <button className="btn small" type="button" onClick={() => mark(r, false)} title="Bring the reminder back">Undo</button>
                  ) : (
                    <button className="btn small" type="button" onClick={() => mark(r, true)}
                            title={r.kind === "due" ? "Paid: stop reminding me" : "Renewed or dealt with: stop reminding me"}>
                      <Icon name="ok" size={14} />{r.kind === "due" ? "Paid" : "Done"}
                    </button>
                  )}
                </div>
              ))}
            </div>
          </section>

          <section className="card">
            <div style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
              <h2 style={{ margin: 0, flex: 1 }}>Recently added</h2>
              <Link href="/documents/" className="btn small">All documents</Link>
            </div>
            {docs === null && <p className="muted">Loading…</p>}
            {docs?.length === 0 && (
              <div className="empty">
                <p style={{ marginTop: 0 }}>No documents yet.</p>
                <Link href="/upload/" className="btn primary"><Icon name="upload" />Upload your first document</Link>
              </div>
            )}
            <div className="rows">
              {docs?.slice(0, 6).map((d) => (
                <div key={d.doc_id} className="row">
                  <FileIcon category={d.category} size={32} />
                  <div className="info">
                    <div className="name">{displayName(d.filename)}</div>
                    <div className="meta">{CATEGORY_LABEL[d.category]} · {d.mine ? "" : `${d.owner}'s · `}added {formatDate(d.added_at)}</div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <section className="card" style={{ alignSelf: "start" }}>
          <h2>Vault status</h2>
          <div className="health">
            <div className="item">
              <span className="dotc" style={{ background: info?.llm_status.ready ? "var(--good)" : "var(--warn)" }} />
              <div><strong>{info?.llm_status.ready ? "Assistant ready" : "Assistant offline"}</strong>
                <div className="muted" style={{ fontSize: 13 }}>{info?.llm_status.message ?? "Checking…"}</div></div>
            </div>
            <div className="item">
              <span className="dotc" style={{ background: info?.ocr_available ? "var(--good)" : "var(--faint)" }} />
              <div><strong>{info?.ocr_available ? "Photos and scans supported" : "Photos and scans not supported yet"}</strong>
                <div className="muted" style={{ fontSize: 13 }}>{info?.ocr_available ? "Tesseract OCR is installed." : <>Install Tesseract: <code>brew install tesseract</code></>}</div></div>
            </div>
            <div className="item">
              <span className="dotc" style={{ background: "var(--good)" }} />
              <div><strong>Private</strong>
                <div className="muted" style={{ fontSize: 13 }}>{info ? `${info.num_chunks} passages, ${formatBytes(size)} of files, ` : ""}all on this computer.</div></div>
            </div>
          </div>
          <Link href="/settings/" className="btn small" style={{ marginTop: 14 }}><Icon name="settings" size={14} />Settings</Link>
        </section>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  return <AppShell title="Dashboard"><Dashboard /></AppShell>;
}
