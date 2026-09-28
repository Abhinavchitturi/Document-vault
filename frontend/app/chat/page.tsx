"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import AppShell, { useVault } from "@/components/AppShell";
import Answer, { citationOrder } from "@/components/Answer";
import { catStyle } from "@/components/DocBits";
import Icon, { StopIcon } from "@/components/Icon";
import PageViewer from "@/components/PageViewer";
import { CATEGORIES, CATEGORY_LABEL, fileUrl, streamChat, type Category, type ChatTurn, type Source } from "@/lib/api";

interface Message {
  role: "user" | "assistant";
  text: string;
  sources?: Source[];
  done?: boolean;
  error?: string;
}

const SUGGESTIONS: [Category, string][] = [
  ["insurance", "When does my bike insurance expire?"],
  ["bill", "What is the electricity bill for August?"],
  ["warranty", "Find the washing machine warranty"],
  ["property", "What is the carpet area of our flat?"],
];
const STORE_KEY = "vault-chat";  // this tab only: closing the tab forgets the chat

const STOPWORDS = new Set(("the a an of in on at to for and or is are was were be by with from as that this what who whom when " +
  "where which why how does did do its it into than then my our we us me find much many month").split(" "));
function termRegex(q: string): RegExp | null {
  const terms = [...new Set(q.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [])].filter((w) => w.length > 2 && !STOPWORDS.has(w));
  return terms.length ? new RegExp(`\\b(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "giu") : null;
}
function highlighted(text: string, re: RegExp | null) {
  if (!re) return text;
  const out: React.ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(re)) {
    const i = m.index ?? 0;
    if (i > last) out.push(text.slice(last, i));
    out.push(<mark key={i}>{m[0]}</mark>);
    last = i + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}
/** When the assistant can't answer: the line in the top passages sharing the most words with the question. */
function closestLine(sources: Source[], question: string) {
  const re = termRegex(question);
  if (!re) return null;
  let best: { line: string; source: Source } | null = null;
  let bestHits = 0;
  for (const s of sources.slice(0, 3)) {
    for (const raw of s.text.split("\n")) {
      const line = raw.trim();
      if (!line || /^SAMPLE DOCUMENT/i.test(line)) continue;
      const count = new Set((line.match(re) ?? []).map((w) => w.toLowerCase())).size;
      if (count > bestHits) { best = { line: line.replace(/^#+\s*/, ""), source: s }; bestHits = count; }
    }
  }
  return best;
}
const sentence = (m: string) => (/[.!?]$/.test(m.trim()) ? m.trim() : `${m.trim()}.`);

function ChatView() {
  const { info, refresh } = useVault();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [category, setCategory] = useState<Category | "">("");
  const [busy, setBusy] = useState(false);
  const [viewing, setViewing] = useState<{ source: Source; answer: string } | null>(null);
  const controller = useRef<AbortController | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const started = useRef(false);

  useEffect(() => {  // restore this tab's chat, then answer a question passed from the dashboard (?q=)
    try {
      const saved = sessionStorage.getItem(STORE_KEY);
      if (saved) setMessages((JSON.parse(saved) as Message[]).filter((m) => m.role === "user" || m.done || m.error));
    } catch { /* ignore */ }
  }, []);
  useEffect(() => {
    if (busy) return;
    try { sessionStorage.setItem(STORE_KEY, JSON.stringify(messages)); } catch { /* ignore */ }
  }, [messages, busy]);

  const scrollDown = () => requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight }));

  const ask = useCallback(async (question: string) => {
    question = question.trim();
    if (!question || controller.current) return;
    // Earlier turns for follow-ups: only questions that got a complete answer.
    const pairs: ChatTurn[] = [];
    messages.forEach((m, i) => {
      const reply = messages[i + 1];
      if (m.role === "user" && reply?.role === "assistant" && reply.done && !reply.error) {
        pairs.push({ role: "user", content: m.text }, { role: "assistant", content: reply.text });
      }
    });

    setMessages((ms) => [...ms, { role: "user", text: question }, { role: "assistant", text: "" }]);
    setBusy(true);
    scrollDown();
    const ctl = new AbortController();
    controller.current = ctl;
    const update = (patch: Partial<Message>) =>
      setMessages((ms) => ms.map((m, i) => (i === ms.length - 1 ? { ...m, ...patch } : m)));

    let text = "";
    try {
      if (info && info.num_documents === 0) {
        update({ error: "Your vault is empty. Upload a policy, warranty card or bill first, then ask again" });
      } else {
        for await (const ev of streamChat(question, pairs, category || null, ctl.signal)) {
          if (ev.type === "sources") update({ sources: ev.sources });
          else if (ev.type === "token") { text += ev.text; update({ text }); scrollDown(); }
          else if (ev.type === "done") update({ done: true });
          else if (ev.type === "error") { update({ error: ev.message }); refresh(); }
        }
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") update({ text: text || "Stopped.", done: true });
      else update({ error: `Couldn't reach the vault: ${(e as Error).message}` });
    } finally {
      controller.current = null;
      setBusy(false);
      scrollDown();
    }
  }, [messages, info, category, refresh]);

  useEffect(() => {
    if (started.current || !info) return;
    const q = new URLSearchParams(window.location.search).get("q");
    started.current = true;
    if (q) {
      window.history.replaceState(null, "", window.location.pathname);
      ask(q);
    }
  }, [info, ask]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (controller.current) { controller.current.abort(); return; }
    if (!input.trim()) return;
    const q = input;
    setInput("");
    ask(q);
  };

  useEffect(() => {  // grow the textarea with its content
    const t = textarea.current;
    if (t) { t.style.height = "auto"; t.style.height = `${Math.min(t.scrollHeight, 180)}px`; }
  }, [input]);

  const newChat = () => {
    controller.current?.abort();
    setMessages([]);
    textarea.current?.focus();
  };

  return (
    <>
      <div className="page" style={{ paddingTop: 0 }}>
        <div className="chat">
          <div className="thread" aria-live="polite">
            {messages.length === 0 ? (
              <section className="welcome">
                <span className="logo"><Icon name="file" size={24} /></span>
                <h2>What would you like to know?</h2>
                <p>{info && info.num_documents === 0
                  ? "Your vault is empty. Upload policies, warranty cards, bills or property papers, then ask me anything about them."
                  : `Ask me anything about your ${info ? `${info.num_documents} ` : ""}documents: expiry dates, amounts, policy numbers, warranties.`}</p>
                {info && !info.llm_status.ready && (
                  <div className="notice"><Icon name="warn" /><span>{sentence(info.llm_status.message)} Until then I can only point you to the closest line in your documents.</span></div>
                )}
                <div className="suggestions">
                  {SUGGESTIONS.map(([cat, q]) => (
                    <button key={q} type="button" className="suggestion" onClick={() => ask(q)}>
                      <small style={{ color: `var(--c-${cat})` }}>{CATEGORY_LABEL[cat]}</small>{q}
                    </button>
                  ))}
                </div>
              </section>
            ) : (
              messages.map((m, i) =>
                m.role === "user"
                  ? <div key={i} className="msg user"><div className="bubble">{m.text}</div></div>
                  : <BotMessage key={i} m={m} question={messages[i - 1]?.text ?? ""} streaming={busy && i === messages.length - 1}
                                onOpen={(source) => setViewing({ source, answer: m.done ? m.text : "" })} />,
              )
            )}
          </div>
          <div className="composer-wrap">
            <form className="composer" onSubmit={submit}>
              <select aria-label="Which documents to use" value={category} onChange={(e) => setCategory(e.target.value as Category | "")}>
                <option value="">All documents</option>
                {CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
              </select>
              <textarea ref={textarea} rows={1} placeholder="Ask about your documents…" aria-label="Message" value={input} autoFocus
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} />
              <button className={`send${busy ? " stop" : ""}`} type="submit" aria-label={busy ? "Stop" : "Send"} disabled={!busy && !input.trim()}>
                {busy ? <StopIcon /> : <Icon name="send" size={18} />}
              </button>
            </form>
            <p className="fineprint">
              Answers come only from your documents, on this computer. Check important dates in the original.
              {messages.length > 0 && <> · <button type="button" className="link-btn" style={{ margin: 0 }} onClick={newChat}>New chat</button></>}
            </p>
          </div>
        </div>
      </div>
      {viewing && <PageViewer source={viewing.source} answer={viewing.answer} onClose={() => setViewing(null)} />}
    </>
  );
}

function BotMessage({ m, question, streaming, onOpen }: { m: Message; question: string; streaming: boolean; onOpen: (s: Source) => void }) {
  const [showPassages, setShowPassages] = useState(false);
  const sources = m.sources ?? [];
  const cited = m.done && !m.error ? citationOrder(m.text, sources.length) : [];
  const hit = m.error ? closestLine(sources, question) : null;
  const shown = cited.length ? cited : sources.map((_, i) => i + 1);
  const re = termRegex(question);
  return (
    <div className="msg">
      <span className="bot-avatar"><Icon name="file" size={16} strokeWidth={2.4} /></span>
      <div className="body">
        {!m.text && !m.error && <span className="typing"><span /><span /><span /></span>}
        {m.text && <Answer text={m.text} numSources={sources.length} streaming={streaming}
                           onCite={(n) => sources[n - 1] && onOpen(sources[n - 1])} />}
        {m.error && <div className="notice">{sentence(m.error)}</div>}
        {hit && (
          <div className="closest">
            <span className="label">Closest line in {hit.source.filename}, page {hit.source.page} (not an AI answer):</span>
            {highlighted(hit.line, re)}
          </div>
        )}
        {cited.length > 0 && (
          <div className="sources">
            {cited.map((n, i) => {
              const s = sources[n - 1];
              return (
                <a key={n} className="src" href={fileUrl(s.doc_id, s.filename, s.page)} target="_blank" rel="noopener"
                   title={`Show ${s.filename}, page ${s.page}, with the answer highlighted`}
                   onClick={(e) => { if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return; e.preventDefault(); onOpen(s); }}>
                  <span className="n" style={catStyle(s.category)}>{i + 1}</span>
                  <span className="t">{s.filename}</span><span className="pg">p. {s.page}</span>
                </a>
              );
            })}
          </div>
        )}
        {(m.done || m.error) && sources.length > 0 && (
          <>
            <button type="button" className="link-btn" onClick={() => setShowPassages(!showPassages)}>
              {showPassages ? "Hide passages" : cited.length ? "Show where this came from" : "Show related passages"}
            </button>
            {showPassages && (
              <div className="passages">
                {shown.map((n) => {
                  const s = sources[n - 1];
                  return (
                    <div key={n} className="passage-card">
                      <div className="ph">{cited.length ? `[${cited.indexOf(n) + 1}] ` : ""}{s.filename} · page {s.page}</div>
                      <div className="pt">{highlighted(s.text, re)}</div>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function ChatPage() {
  return <AppShell title="Chat"><ChatView /></AppShell>;
}
