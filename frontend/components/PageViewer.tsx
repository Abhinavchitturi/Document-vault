"use client";

import { useEffect, useRef, useState } from "react";
import { api, CATEGORY_LABEL, fileUrl, hardToRead, pageImageUrl, type Highlights, type Source } from "@/lib/api";
import { FileIcon } from "./DocBits";
import Icon from "./Icon";

/** A source page with the passage (light) and the answer's facts (strong) highlighted. */
export default function PageViewer({ source, answer, onClose }: { source: Source; answer: string; onClose: () => void }) {
  const [page, setPage] = useState(source.page);
  const [h, setH] = useState<Highlights | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onSourcePage = page === source.page;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      previous?.focus();
    };
  }, [onClose]);

  useEffect(() => {
    let alive = true;
    setH(null);
    setError(null);
    api.highlights(source.doc_id, page, onSourcePage ? { chunk_id: source.chunk_id, answer } : {})
      .then((r) => alive && setH(r))
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => { alive = false; };
  }, [source, page, answer, onSourcePage]);

  const scrollToHighlight = () => {
    const body = bodyRef.current;
    const target = body?.querySelector<HTMLElement>(".hl-fact, .tp-fact") ?? body?.querySelector<HTMLElement>(".hl-passage, .tp-passage");
    if (body && target) {
      body.scrollTop = Math.max(0, target.offsetTop - body.clientHeight / 3);
      body.scrollLeft = Math.max(0, target.offsetLeft - body.clientWidth / 4);
    }
  };
  useEffect(() => { if (h?.kind === "text") scrollToHighlight(); }, [h]);

  const pct = (v: number, of: number) => `${(v / of) * 100}%`;
  const box = (cls: string, [x, y, w, hh]: number[], i: number) =>
    h && <div key={`${cls}${i}`} className={`hl ${cls}`} style={{ left: pct(x, h.width), top: pct(y, h.height), width: pct(w, h.width), height: pct(hh, h.height) }} />;

  return (
    <div className="viewer" role="dialog" aria-modal="true" aria-labelledby="viewer-name" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="viewer-panel">
        <div className="viewer-head">
          <FileIcon category={source.category} />
          <div className="viewer-title">
            <span className="t" id="viewer-name">{source.filename}</span>
            <span className="s">{CATEGORY_LABEL[source.category] ?? source.category} · page {page}</span>
          </div>
          {h && h.pages > 1 && (
            <span className="pager">
              <button className="icon-btn" type="button" aria-label="Previous page" disabled={page <= 1} onClick={() => setPage(page - 1)}><Icon name="prev" size={14} /></button>
              {page} / {h.pages}
              <button className="icon-btn" type="button" aria-label="Next page" disabled={page >= h.pages} onClick={() => setPage(page + 1)}><Icon name="next" size={14} /></button>
            </span>
          )}
          <a className="btn small" href={fileUrl(source.doc_id, source.filename, page)} target="_blank" rel="noopener">
            <Icon name="open" size={14} /><span className="label">Open original</span>
          </a>
          <button ref={closeRef} className="icon-btn" type="button" aria-label="Close" onClick={onClose}><Icon name="close" /></button>
        </div>

        {h && <Note h={h} onSourcePage={onSourcePage} sourcePage={source.page} />}

        <div className="viewer-body" ref={bodyRef}>
          {error && <div className="viewer-loading" style={{ color: "var(--bad)" }}>Couldn&apos;t show this page: {error}</div>}
          {!h && !error && <div className="viewer-loading">Loading page…</div>}
          {h?.kind === "text" && <TextPage h={h} />}
          {h && h.kind !== "text" && (
            <div className="page-wrap">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img alt={`${source.filename}, page ${page}`} onLoad={scrollToHighlight}
                   src={h.kind === "image" ? fileUrl(source.doc_id, source.filename) : pageImageUrl(source.doc_id, page)} />
              {h.passage.map((r, i) => box("hl-passage", r, i))}
              {h.facts.map((r, i) => box("hl-fact", r, i))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Note({ h, onSourcePage, sourcePage }: { h: Highlights; onSourcePage: boolean; sourcePage: number }) {
  let content: React.ReactNode;
  const blurry = h.kind === "scan" && hardToRead(h.confidence);
  if (h.kind !== "scan" && h.scanned) {
    content = "This page is a scan or photo. Install Tesseract (brew install tesseract) to read and highlight it.";
  } else if (!onSourcePage) {
    content = `Page ${h.page}. The answer came from page ${sourcePage}.`;
  } else {
    content = (
      <>
        {h.facts_found.length > 0 && (
          <span className="key"><span className="sw fact" />Answer: <strong>{h.facts_found.join(" · ")}</strong></span>
        )}
        {h.passage_found && <span className="key"><span className="sw passage" />Passage the answer used</span>}
        {h.facts_missing.length > 0 && !h.facts_found.length && (
          <span>The answer&apos;s exact wording wasn&apos;t found on this page, so check the highlighted passage.</span>
        )}
        {!h.passage_found && !h.facts_found.length && "Nothing to highlight on this page."}
        {h.kind === "scan" && !blurry && <span className="faint">Read from a scan, so the boxes are approximate.</span>}
      </>
    );
  }
  return (
    <>
      {blurry && (
        <div className="viewer-note warn"><Icon name="warn" size={14} />
          This scan or photo is hard to read ({h.confidence}% OCR confidence). Check the numbers against the page, and consider uploading a clearer photo.</div>
      )}
      <div className="viewer-note">{content}</div>
    </>
  );
}

function TextPage({ h }: { h: Highlights }) {
  const text = h.text ?? "";
  const level = new Uint8Array(text.length);
  h.passage.forEach(([s, e]) => level.fill(1, s, e));
  h.facts.forEach(([s, e]) => level.fill(2, s, e));
  const parts: React.ReactNode[] = [];
  for (let i = 0; i < text.length;) {
    let j = i;
    while (j < text.length && level[j] === level[i]) j++;
    const piece = text.slice(i, j);
    parts.push(level[i] === 0 ? piece : <span key={i} className={level[i] === 2 ? "tp-fact" : "tp-passage"}>{piece}</span>);
    i = j;
  }
  return <div className="page-text">{parts}</div>;
}
