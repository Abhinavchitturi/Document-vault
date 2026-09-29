"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import AppShell, { useVault } from "@/components/AppShell";
import Icon from "@/components/Icon";
import { api, CATEGORIES, CATEGORY_LABEL, hardToRead, type Category, type UploadResult } from "@/lib/api";

function Upload() {
  const { info, refresh } = useVault();
  const [category, setCategory] = useState<Category | "auto">("auto");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState<number>(0);
  const [results, setResults] = useState<UploadResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const send = async (list: FileList | null) => {
    const files = Array.from(list ?? []);
    if (!files.length) return;
    setBusy(files.length);
    setError(null);
    try {
      setResults(await api.upload(files, category));
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(0);
      if (input.current) input.current.value = "";
    }
  };
  const added = results.filter((r) => r.status === "added").length;

  return (
    <div className="page" style={{ maxWidth: 760 }}>
      <label className={`dropzone${over ? " over" : ""}`}
             onDragEnter={(e) => { e.preventDefault(); setOver(true); }} onDragOver={(e) => { e.preventDefault(); setOver(true); }}
             onDragLeave={(e) => { e.preventDefault(); setOver(false); }}
             onDrop={(e) => { e.preventDefault(); setOver(false); send(e.dataTransfer.files); }}>
        <input ref={input} type="file" multiple hidden onChange={(e) => send(e.target.files)} disabled={busy > 0} />
        {busy ? <span className="spinner" style={{ width: 28, height: 28, color: "var(--accent)" }} /> : <Icon name="upload" size={32} />}
        <strong>{busy ? `Reading and indexing ${busy} ${busy === 1 ? "file" : "files"}…` : "Drop files here or click to choose"}</strong>
        <span className="muted">
          {info?.ocr_available ? "PDF, Word, text, or photos of documents" : "PDF, Word or text files"} · up to {info?.max_upload_mb ?? 50} MB each
        </span>
      </label>

      <div className="upload-options">
        <label htmlFor="upload-type" className="muted">File as</label>
        <select id="upload-type" className="input" value={category} onChange={(e) => setCategory(e.target.value as Category | "auto")}>
          <option value="auto">Detect automatically</option>
          {CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
        </select>
        <span className="faint" style={{ fontSize: 13 }}>You can change a document&apos;s type later on the Documents page.</span>
      </div>

      {info && !info.ocr_available && (
        <div className="notice" style={{ marginBottom: 12 }}><Icon name="warn" />
          <span>Photos and scanned PDFs need Tesseract. Run <code>brew install tesseract</code>, then restart the app.</span></div>
      )}
      {error && <div className="notice bad" style={{ marginBottom: 12 }}><Icon name="warn" />Upload failed: {error}</div>}

      {results.length > 0 && (
        <section className="card">
          <h2>Result</h2>
          <div className="results">
            {results.map((r, i) => (
              <div key={i} className={`result ${r.status}`}>
                <Icon name={r.status === "error" ? "warn" : "ok"} size={15} />
                <span>
                  <span className="fname">{r.filename}</span>
                  {r.status === "added" && r.document && ` added as ${CATEGORY_LABEL[r.document.category]}, ${r.document.pages} ${r.document.pages === 1 ? "page" : "pages"}${r.document.ocr_pages ? " (read with OCR)" : ""}.`}
                  {r.status === "added" && r.document?.needs_review &&
                    " We weren't sure about the type: check it in Documents."}
                  {r.status === "added" && hardToRead(r.document?.ocr_confidence) &&
                    ` It was hard to read (${r.document!.ocr_confidence}% OCR confidence), so answers from it may be wrong. A sharper, well-lit photo will help.`}
                  {r.status === "duplicate" && " is already in the vault."}
                  {r.status === "error" && `: ${r.error}`}
                </span>
              </div>
            ))}
          </div>
          {added > 0 && (
            <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
              <Link href="/chat/" className="btn primary">Ask about {added === 1 ? "it" : "them"}</Link>
              <Link href="/documents/" className="btn">See all documents</Link>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

export default function UploadPage() {
  return <AppShell title="Upload"><Upload /></AppShell>;
}
