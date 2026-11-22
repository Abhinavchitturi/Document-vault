"use client";

import { useEffect, useState } from "react";
import { api, type AIDownloadStatus } from "@/lib/api";
import { useVault } from "./AppShell";
import Icon from "./Icon";

/** The one-click "Download the AI" button, with its progress, wherever answers aren't available yet. */
export default function AIDownload({ compact = false }: { compact?: boolean }) {
  const { info, refresh } = useVault();
  const [status, setStatus] = useState<AIDownloadStatus | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const canDownload = !!info?.llm_status.can_download;

  useEffect(() => {
    if (!canDownload) return;
    let alive = true;
    const poll = async () => {
      try {
        const s = await api.aiDownloadStatus();
        if (!alive) return;
        setStatus(s);
        if (s.state === "done") refresh();
        if (s.state === "downloading") setTimeout(poll, 1000);
      } catch { /* the vault restarted: the next page load asks again */ }
    };
    poll();
    return () => { alive = false; };
  }, [canDownload, refresh]);

  if (!canDownload && status?.state !== "downloading") return null;

  const start = async () => {
    setProblem(null);
    try {
      setStatus(await api.aiDownload());
      const follow = async () => {
        const s = await api.aiDownloadStatus();
        setStatus(s);
        if (s.state === "downloading") setTimeout(follow, 1000);
        else if (s.state === "done") refresh();
      };
      setTimeout(follow, 1000);
    } catch (e) {
      setProblem((e as Error).message);
    }
  };

  const downloading = status?.state === "downloading";
  const share = status && status.total ? Math.round((status.done / status.total) * 100) : 0;
  const what = status?.step === "model" ? "the answer model" : "llama.cpp";
  return (
    <div className="ai-download" style={{ display: "grid", gap: 10, marginTop: compact ? 8 : 0 }}>
      {downloading ? (
        <>
          <span className="muted" style={{ fontSize: 13 }}>
            Downloading {what}… {share}%{status?.total ? ` of ${(status.total / 1e9).toFixed(status.total > 1e9 ? 1 : 2)} GB` : ""}.
            You can keep using the vault; answers start by themselves when it's done.
          </span>
          <progress value={share} max={100} style={{ width: "100%", accentColor: "var(--accent)" }} />
        </>
      ) : info?.is_admin ? (
        <div className="row" style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button className="btn primary" type="button" onClick={start}><Icon name="upload" size={14} />Download the AI (about 5 GB)</button>
          <span className="muted" style={{ fontSize: 13 }}>Once. After that it works offline, on this computer.</span>
        </div>
      ) : (
        <span className="muted" style={{ fontSize: 13 }}>Ask the vault's admin to download the AI in Settings.</span>
      )}
      {(problem || status?.state === "failed") && (
        <div className="notice bad" role="status"><Icon name="warn" />{problem ?? status?.error}</div>
      )}
    </div>
  );
}
