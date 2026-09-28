"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell, { useVault } from "@/components/AppShell";
import { displayName, FileIcon } from "@/components/DocBits";
import Icon from "@/components/Icon";
import PageViewer from "@/components/PageViewer";
import { api, CATEGORIES, CATEGORY_LABEL, fileUrl, formatBytes, formatDate, type Category, type Source, type VaultDocument } from "@/lib/api";

function Documents() {
  const { refresh } = useVault();
  const [docs, setDocs] = useState<VaultDocument[] | null>(null);
  const [filter, setFilter] = useState<Category | "">("");
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [viewing, setViewing] = useState<Source | null>(null);

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
  const shown = (docs ?? []).filter((d) => (!filter || d.category === filter) && d.filename.toLowerCase().includes(query.toLowerCase()));

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
                <tr key={d.doc_id}>
                  <td className="c-name">
                    <div className="namecell">
                      <FileIcon category={d.category} size={32} />
                      <div>
                        <div className="name">{displayName(d.filename)}</div>
                        <div className="faint" style={{ fontSize: 12 }}>
                          {d.pages} {d.pages === 1 ? "page" : "pages"}{d.ocr_pages ? " · scanned" : ""}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td>
                    <select aria-label={`Type of ${d.filename}`} value={d.category} onChange={(e) => changeCategory(d, e.target.value as Category)}>
                      {CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
                    </select>
                  </td>
                  <td className="c-added faint">{formatDate(d.added_at)}</td>
                  <td className="c-size faint">{formatBytes(d.size_bytes)}</td>
                  <td>
                    <div className="actions">
                      <button className="icon-btn" type="button" title="View" aria-label={`View ${d.filename}`} onClick={() => view(d)}><Icon name="search" /></button>
                      <a className="icon-btn" href={fileUrl(d.doc_id, d.filename)} target="_blank" rel="noopener" title="Open original" aria-label={`Open ${d.filename}`}><Icon name="open" /></a>
                      <button className="icon-btn danger" type="button" title="Remove from vault" aria-label={`Remove ${d.filename}`} onClick={() => remove(d)}><Icon name="trash" /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {viewing && <PageViewer source={viewing} answer="" onClose={() => setViewing(null)} />}
    </div>
  );
}

export default function DocumentsPage() {
  return (
    <AppShell title="Documents" actions={<Link href="/upload/" className="btn primary small"><Icon name="plus" size={14} />Upload</Link>}>
      <Documents />
    </AppShell>
  );
}
