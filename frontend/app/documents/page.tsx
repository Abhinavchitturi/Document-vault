"use client";

import Link from "next/link";
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import AppShell, { useVault } from "@/components/AppShell";
import { displayName, FileIcon } from "@/components/DocBits";
import Icon from "@/components/Icon";
import PageViewer from "@/components/PageViewer";
import { api, CATEGORIES, CATEGORY_LABEL, daysUntil, fileUrl, formatAmount, formatBytes, formatDate, hardToRead, showReference, type Category, type DocumentChanges, type Person, type Source, type VaultDocument } from "@/lib/api";

function Documents() {
  const { refresh } = useVault();
  const [docs, setDocs] = useState<VaultDocument[] | null>(null);
  const [filter, setFilter] = useState<Category | "">("");
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [viewing, setViewing] = useState<Source | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [sharing, setSharing] = useState<string | null>(null);
  const [family, setFamily] = useState<Person[] | null>(null);
  const [whose, setWhose] = useState<"all" | "mine" | "shared">("all");

  const openSharing = (d: VaultDocument) => {
    setEditing(null);
    setSharing(sharing === d.doc_id ? null : d.doc_id);
    if (family === null) api.family().then(setFamily).catch(() => setFamily([]));
  };
  const saveSharing = async (d: VaultDocument, ids: number[]) => {
    try {
      const updated = await api.share(d.doc_id, ids);
      setDocs((all) => all?.map((x) => (x.doc_id === d.doc_id ? updated : x)) ?? null);
      setSharing(null);
      setMessage(updated.shared_with.length
        ? `${d.filename} is shared with ${updated.shared_with.map((p) => p.name).join(", ")}.`
        : `${d.filename} is only yours again.`);
    } catch (e) {
      setMessage(`Couldn't share: ${(e as Error).message}`);
    }
  };

  const saveDetails = async (d: VaultDocument, changes: DocumentChanges) => {
    try {
      const updated = await api.updateDocument(d.doc_id, changes);
      setDocs((all) => all?.map((x) => (x.doc_id === d.doc_id ? updated : x)) ?? null);
      setEditing(null);
      setMessage(`Saved the details of ${d.filename}.`);
    } catch (e) {
      setMessage(`Couldn't save: ${(e as Error).message}`);
    }
  };

  const load = useCallback(() => api.documents().then(setDocs).catch((e) => setMessage(String(e.message ?? e))), []);
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("type");
    if (t && (CATEGORIES as readonly string[]).includes(t)) setFilter(t as Category);
    load();
  }, [load]);

  const counts = useMemo(() => {
    const c = Object.fromEntries(CATEGORIES.map((k) => [k, 0])) as Record<Category, number>;
    docs?.forEach((d) => { c[d.category]++; });
    return c;
  }, [docs]);
  const anyShared = (docs ?? []).some((d) => !d.mine);
  const shown = (docs ?? []).filter((d) => (!filter || d.category === filter) && d.filename.toLowerCase().includes(query.toLowerCase())
    && (whose === "all" || (whose === "mine") === d.mine));

  const changeCategory = async (d: VaultDocument, category: Category) => {
    try {
      const updated = await api.setCategory(d.doc_id, category);
      setDocs((all) => all?.map((x) => (x.doc_id === d.doc_id ? updated : x)) ?? null);
      setMessage(`${d.filename} is now filed under ${CATEGORY_LABEL[category]}.`);
      refresh();
    } catch (e) {
      setMessage(`Couldn't change the type: ${(e as Error).message}`);
    }
  };
  const remove = async (d: VaultDocument) => {
    if (!d.mine) {
      if (!confirm(`Take "${d.filename}" off your list?\n\n${d.owner} keeps it; they can share it with you again.`)) return;
      try {
        await api.leaveShare(d.doc_id);
        setMessage(`Removed ${d.filename} from your list.`);
      } catch (e) {
        setMessage(`Couldn't remove it: ${(e as Error).message}`);
      }
      load();
      refresh();
      return;
    }
    if (!confirm(`Remove "${d.filename}" from the vault?\n\nThe copy on your computer outside this app is not affected.`)) return;
    await api.deleteDocument(d.doc_id);
    setMessage(`Removed ${d.filename}.`);
    load();
    refresh();
  };
  const view = (d: VaultDocument) =>
    setViewing({ rank: 0, score: 0, chunk_id: "", doc_id: d.doc_id, filename: d.filename, category: d.category, page: 1, text: "" });

  return (
    <div className="page">
      <div className="toolbar">
        <input className="input search" type="search" placeholder="Find by file name…" value={query}
               onChange={(e) => setQuery(e.target.value)} aria-label="Find by file name" />
        <div className="filters" role="group" aria-label="Filter by type">
          <button type="button" className="filter" aria-pressed={!filter} onClick={() => setFilter("")}>
            All <span className="count">{docs?.length ?? 0}</span>
          </button>
          {CATEGORIES.map((c) => (
            <button key={c} type="button" className="filter" aria-pressed={filter === c} disabled={!counts[c] && filter !== c}
                    onClick={() => setFilter(c)}>
              {CATEGORY_LABEL[c]} <span className="count">{counts[c]}</span>
            </button>
          ))}
        </div>
        {anyShared && (
          <div className="segmented" role="group" aria-label="Whose documents">
            {([["all", "All"], ["mine", "Mine"], ["shared", "Shared with me"]] as const).map(([k, label]) => (
              <button key={k} type="button" aria-pressed={whose === k} onClick={() => setWhose(k)}>{label}</button>
            ))}
          </div>
        )}
      </div>
      {message && <div className="notice good" style={{ marginBottom: 12 }} role="status"><Icon name="ok" />{message}</div>}

      <div className="card" style={{ padding: "14px 16px" }}>
        {docs === null ? <p className="muted">Loading…</p> : shown.length === 0 ? (
          <div className="empty">
            {docs.length === 0 ? <>No documents yet. <Link href="/upload/">Upload some</Link>.</> : "Nothing matches."}
          </div>
        ) : (
          <table className="doc-table">
            <thead>
              <tr><th>Name</th><th>Type</th><th className="c-added">Added</th><th className="c-size">Size</th><th aria-label="Actions" /></tr>
            </thead>
            <tbody>
              {shown.map((d) => (
                <Fragment key={d.doc_id}>
                <tr>
                  <td className="c-name">
                    <div className="namecell">
                      <FileIcon category={d.category} size={32} />
                      <div>
                        <div className="name">{displayName(d.filename)}</div>
                        <div className="faint" style={{ fontSize: 12 }}>
                          {d.pages} {d.pages === 1 ? "page" : "pages"}{d.ocr_pages ? " · scanned" : ""}
                          {d.needs_review && (
                            <span className="hard-to-read" title={`Sorted automatically (${d.category_confidence}% sure). Pick the right type in the menu.`}>
                              {" · "}<Icon name="warn" size={11} /> check type
                            </span>
                          )}
                          {hardToRead(d.ocr_confidence) && (
                            <span className="hard-to-read" title={`OCR confidence ${d.ocr_confidence}%: answers from this document may be wrong. Upload a clearer photo.`}>
                              {" · "}<Icon name="warn" size={11} /> hard to read
                            </span>
                          )}
                        </div>
                        <Facts d={d} />
                        {!d.mine && <div className="shared-line"><Icon name="share" size={12} /> Shared by {d.owner}</div>}
                        {d.mine && d.shared_with.length > 0 && (
                          <div className="shared-line"><Icon name="share" size={12} /> Shared with {d.shared_with.map((p) => p.name).join(", ")}</div>
                        )}
                      </div>
                    </div>
                  </td>
                  <td>
                    {d.mine ? (
                      <select aria-label={`Type of ${d.filename}`} value={d.category} onChange={(e) => changeCategory(d, e.target.value as Category)}>
                        {CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
                      </select>
                    ) : <span className="muted">{CATEGORY_LABEL[d.category]}</span>}
                  </td>
                  <td className="c-added faint">{formatDate(d.added_at)}</td>
                  <td className="c-size faint">{formatBytes(d.size_bytes)}</td>
                  <td>
                    <div className="actions">
                      <button className="icon-btn" type="button" title="View" aria-label={`View ${d.filename}`} onClick={() => view(d)}><Icon name="search" /></button>
                      {d.mine && <>
                        <button className="icon-btn" type="button" title="Details: dates, amount, number" aria-label={`Details of ${d.filename}`}
                                aria-expanded={editing === d.doc_id} onClick={() => { setSharing(null); setEditing(editing === d.doc_id ? null : d.doc_id); }}><Icon name="edit" /></button>
                        <button className="icon-btn" type="button" title="Share with family" aria-label={`Share ${d.filename}`}
                                aria-expanded={sharing === d.doc_id} onClick={() => openSharing(d)}><Icon name="share" /></button>
                      </>}
                      <a className="icon-btn" href={fileUrl(d.doc_id, d.filename)} target="_blank" rel="noopener" title="Open original" aria-label={`Open ${d.filename}`}><Icon name="open" /></a>
                      {d.mine
                        ? <button className="icon-btn danger" type="button" title="Remove from vault" aria-label={`Remove ${d.filename}`} onClick={() => remove(d)}><Icon name="trash" /></button>
                        : <button className="icon-btn" type="button" title="Remove from my list" aria-label={`Remove ${d.filename} from my list`} onClick={() => remove(d)}><Icon name="close" /></button>}
                    </div>
                  </td>
                </tr>
                {sharing === d.doc_id && (
                  <tr className="details-row"><td colSpan={5}>
                    <ShareForm d={d} family={family} onSave={(ids) => saveSharing(d, ids)} onCancel={() => setSharing(null)} />
                  </td></tr>
                )}
                {editing === d.doc_id && (
                  <tr className="details-row"><td colSpan={5}><DetailsForm d={d} onSave={(c) => saveDetails(d, c)} onCancel={() => setEditing(null)} /></td></tr>
                )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {viewing && <PageViewer source={viewing} answer="" onClose={() => setViewing(null)} />}
    </div>
  );
}

/** Expiry, due date, amount, number: what the vault read from the document (Phase 9). */
function Facts({ d }: { d: VaultDocument }) {
  const when = (iso: string, label: string) => {
    const left = daysUntil(iso);
    return <span className={left < 0 ? "past" : left <= 30 ? "soon" : undefined}>{label} {formatDate(iso)}</span>;
  };
  if (!d.expires_on && !d.due_on && !d.amount && !d.reference) return null;
  return (
    <div className="facts">
      {d.expires_on && when(d.expires_on, d.category === "warranty" ? "Warranty until" : "Expires")}
      {d.due_on && when(d.due_on, "Due")}
      {d.amount && <span>{formatAmount(d.amount)}</span>}
      {d.reference && <span title="The full number is in the details (pencil)">No. {showReference(d.reference)}</span>}
    </div>
  );
}

/** Pick who else can see one of your documents. */
function ShareForm({ d, family, onSave, onCancel }:
  { d: VaultDocument; family: Person[] | null; onSave: (ids: number[]) => void; onCancel: () => void }) {
  const [chosen, setChosen] = useState<Set<number>>(new Set(d.shared_with.map((p) => p.id)));
  const toggle = (id: number) => setChosen((c) => { const n = new Set(c); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  if (family === null) return <p className="muted" style={{ margin: 0 }}>Loading…</p>;
  if (family.length === 0) {
    return (
      <div className="share-form">
        <p className="muted" style={{ margin: 0 }}>There&apos;s nobody to share with yet. The admin can add family members in Settings.</p>
        <div><button className="btn" type="button" onClick={onCancel}>Close</button></div>
      </div>
    );
  }
  return (
    <form className="share-form" onSubmit={(e) => { e.preventDefault(); onSave([...chosen]); }}>
      <strong style={{ fontSize: 14 }}>Who else can see “{d.filename}”?</strong>
      <div className="share-people">
        {family.map((p) => (
          <label key={p.id} className="check">
            <input type="checkbox" checked={chosen.has(p.id)} onChange={() => toggle(p.id)} />{p.name}
          </label>
        ))}
      </div>
      <p className="faint" style={{ margin: 0, fontSize: 12.5 }}>
        They can view it, search it and ask questions about it, and get its reminders. Only you can change or delete it.
      </p>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn primary" type="submit">Save</button>
        <button className="btn" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function DetailsForm({ d, onSave, onCancel }: { d: VaultDocument; onSave: (c: DocumentChanges) => void; onCancel: () => void }) {
  const [f, setF] = useState({
    issuer: d.issuer ?? "", reference: d.reference ?? "", amount: d.amount ?? "",
    expires_on: d.expires_on ?? "", due_on: d.due_on ?? "",
  });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    onSave({ issuer: f.issuer || null, reference: f.reference || null, amount: f.amount || null,
             expires_on: f.expires_on || null, due_on: f.due_on || null });
  };
  return (
    <form className="details-form" onSubmit={submit}>
      <label className="wide">From (company or office)<input className="input" value={f.issuer} onChange={set("issuer")} maxLength={200} /></label>
      <label>Policy / account / serial number<input className="input" value={f.reference} onChange={set("reference")} maxLength={120} /></label>
      <label>Amount (Rs.)<input className="input" value={f.amount} onChange={set("amount")} inputMode="decimal" maxLength={32} /></label>
      <label>Expires / warranty until<input className="input" type="date" value={f.expires_on} onChange={set("expires_on")} /></label>
      <label>Payment due<input className="input" type="date" value={f.due_on} onChange={set("due_on")} /></label>
      <p className="faint wide" style={{ margin: 0, fontSize: 12.5 }}>
        {d.facts_method === "person" ? "Corrected by you." : d.facts_method ? `Read automatically (${d.facts_method === "needle" ? "Needle 3" : d.facts_method === "ollama" ? "answer model" : "text patterns"}). Fix anything that's wrong; the vault won't overwrite your changes.` : ""}
      </p>
      <div className="wide" style={{ display: "flex", gap: 8 }}>
        <button className="btn primary" type="submit">Save</button>
        <button className="btn" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

export default function DocumentsPage() {
  return (
    <AppShell title="Documents" actions={<Link href="/upload/" className="btn primary small"><Icon name="plus" size={14} />Upload</Link>}>
      <Documents />
    </AppShell>
  );
}
