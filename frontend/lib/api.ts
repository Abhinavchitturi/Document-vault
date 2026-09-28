// Typed client for the vault's FastAPI backend (backend/api/app.py). Same origin: the Python
// server serves these pages, and `npm run dev` forwards /api to it.

export const CATEGORIES = ["insurance", "warranty", "bill", "property", "other"] as const;
export type Category = (typeof CATEGORIES)[number];
export const CATEGORY_LABEL: Record<Category, string> = {
  insurance: "Insurance",
  warranty: "Warranties",
  bill: "Bills",
  property: "Property",
  other: "Other",
};

export interface LlmStatus {
  ready: boolean;
  message: string;
}

export interface Info {
  model: string;
  owner: string;
  llm: string | null;
  llm_status: LlmStatus;
  num_documents: number;
  num_chunks: number;
  categories: Record<Category, number>;
  ocr_available: boolean;
  supported_types: string[];
  max_upload_mb: number;
}

export interface VaultDocument {
  doc_id: string;
  filename: string;
  category: Category;
  owner: string;
  pages: number;
  ocr_pages: number;
  num_chunks: number;
  size_bytes: number;
  added_at: string;
}

export interface Source {
  rank: number;
  score: number;
  chunk_id: string;
  doc_id: string;
  filename: string;
  category: Category;
  page: number;
  text: string;
}

export interface UploadResult {
  filename: string;
  status: "added" | "duplicate" | "error";
  document?: VaultDocument;
  error?: string;
}

export interface Settings {
  owner: string;
  llm_model: string | null;
  llm_models: string[];
  llm_status: LlmStatus;
  embedding_model: string;
  database: string;
  index_dir: string;
  ocr_available: boolean;
}

export interface Highlights {
  kind: "pdf" | "text" | "image";
  page: number;
  pages: number;
  width: number;
  height: number;
  passage: number[][];
  facts: number[][];
  facts_found: string[];
  facts_missing: string[];
  passage_found: boolean;
  scanned: boolean;
  text: string | null;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

async function json<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`);
  return body as T;
}

export const api = {
  info: () => fetch("/api/info").then((r) => json<Info>(r)),
  documents: () => fetch("/api/documents").then((r) => json<VaultDocument[]>(r)),
  deleteDocument: (id: string) =>
    fetch(`/api/documents/${encodeURIComponent(id)}`, { method: "DELETE" }).then((r) => json<{ deleted: string }>(r)),
  setCategory: (id: string, category: Category) =>
    fetch(`/api/documents/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category }),
    }).then((r) => json<VaultDocument>(r)),
  upload: (files: File[], category: Category | "auto") => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    form.append("category", category);
    return fetch("/api/documents", { method: "POST", body: form }).then((r) => json<UploadResult[]>(r));
  },
  settings: () => fetch("/api/settings").then((r) => json<Settings>(r)),
  saveSettings: (llm_model: string) =>
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ llm_model }),
    }).then((r) => json<Settings>(r)),
  highlights: (docId: string, page: number, body: { chunk_id?: string; answer?: string }) =>
    fetch(`/api/documents/${encodeURIComponent(docId)}/pages/${page}/highlights`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<Highlights>(r)),
};

export const fileUrl = (docId: string, filename: string, page?: number) => {
  const url = `/api/documents/${encodeURIComponent(docId)}/file`;
  return /\.pdf$/i.test(filename) && page ? `${url}#page=${page}` : url;
};
export const pageImageUrl = (docId: string, page: number) =>
  `/api/documents/${encodeURIComponent(docId)}/pages/${page}/image`;

export type ChatEvent =
  | { type: "sources"; sources: Source[] }
  | { type: "token"; text: string }
  | { type: "done"; citations: number[]; found: boolean; llm: string }
  | { type: "error"; message: string };

/** POST /api/chat and yield its NDJSON events as they arrive (answer text streams word by word). */
export async function* streamChat(
  question: string,
  history: ChatTurn[],
  category: Category | null,
  signal: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, history: history.slice(-6), category }),
    signal,
  });
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}));
    yield { type: "error", message: typeof body.detail === "string" ? body.detail : `Something went wrong (${res.status})` };
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) if (line.trim()) yield JSON.parse(line) as ChatEvent;
  }
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
