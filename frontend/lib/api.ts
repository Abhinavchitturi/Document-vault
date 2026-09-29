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

export interface User {
  id: number;
  name: string;
  is_admin: boolean;
}

export interface Member extends User {
  can_sign_in: boolean;
}

export interface AuthStatus {
  user: User | null;
  setup_needed: boolean;
  suggested_name: string | null;
  passkeys_available: boolean; // false when opened as 127.0.0.1: passkeys need "localhost"
  passkey_hint: string | null;
}

export interface Passkey {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
}

export interface Device {
  id: number;
  name: string;
  created_at: string;
  last_seen_at: string | null;
}

export interface Upcoming {
  doc_id: string;
  filename: string;
  category: Category;
  kind: "expires" | "due";
  date: string;
  days_left: number;
  amount: string | null;
  issuer: string | null;
}

export interface Reminder {
  doc_id: string;
  filename: string;
  category: Category;
  kind: "expires" | "due";
  date: string;
  days_left: number;
  done: boolean;
  status: "overdue" | "today" | "soon" | "upcoming" | "done";
  next_notice: string | null;
  title: string;
  body: string;
  amount: string | null;
  issuer: string | null;
}

export interface ReminderSettings {
  expiry_days: number[];
  due_days: number[];
  time_of_day: string;
  desktop: boolean;
}

export type DocumentChanges = Partial<Pick<VaultDocument, "category" | "expires_on" | "due_on" | "amount" | "reference" | "issuer">>;

export interface PhoneAccess {
  enabled: boolean;
  url: string | null;
  urls: string[];
  tunnel: boolean; // reachable through Tailscale, so phones work away from home
  devices: Device[];
}

export interface Pairing {
  code: string;
  expires_in: number;
  url: string;
  fingerprint: string;
  qr_svg: string;
}

interface Ceremony {
  ceremony: string;
  options: string; // WebAuthn options as JSON text
}

export interface Info {
  model: string;
  owner: string;
  is_admin: boolean;
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
  ocr_confidence: number | null; // worst OCR page, 0-100; null when nothing was OCR'd
  category_source: "auto" | "person" | null; // who chose the type (null: added before Phase 8)
  category_confidence: number | null;
  needs_review: boolean; // sorted automatically, but not sure: ask the person to check the type
  // Phase 9: key facts read from the document (or corrected by a person)
  expires_on: string | null; // YYYY-MM-DD
  due_on: string | null;
  amount: string | null; // digits, e.g. "2486"
  reference: string | null;
  issuer: string | null;
  facts_method: "needle" | "ollama" | "rules" | "person" | null;
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
  is_admin: boolean;
  llm_model: string | null;
  llm_models: string[];
  llm_status: LlmStatus;
  embedding_model: string;
  database: string;
  index_dir: string;
  ocr_available: boolean;
  search_method: "hybrid" | "vector" | "keyword";
  facts_reader: "needle" | "ollama" | "rules";
}

export interface Highlights {
  kind: "pdf" | "scan" | "text" | "image"; // scan: OCR'd page with word boxes; image: can't be read here
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
  confidence: number | null;
}

/** Below this OCR confidence, a scan is flagged as hard to read (backend/parser/ocr.py). */
export const LOW_CONFIDENCE = 70;
export const hardToRead = (confidence: number | null | undefined) => confidence != null && confidence < LOW_CONFIDENCE;

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

/** Signed out (session expired, password changed elsewhere): go to the sign-in screen, then come back. */
export function toSignIn() {
  if (typeof window === "undefined" || window.location.pathname.startsWith("/login")) return;
  window.location.replace(`/login/?next=${encodeURIComponent(window.location.pathname)}`);
}

async function json<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (res.status === 401 && !res.url.includes("/api/auth/")) toSignIn();
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`);
  return body as T;
}

const postJson = (url: string, body: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  authStatus: () => fetch("/api/auth/status").then((r) => json<AuthStatus>(r)),
  setup: (name: string, password: string) => postJson("/api/auth/setup", { name, password }).then((r) => json<{ user: User }>(r)),
  login: (name: string, password: string) => postJson("/api/auth/login", { name, password }).then((r) => json<{ user: User }>(r)),
  logout: () => fetch("/api/auth/logout", { method: "POST" }).then((r) => json<{ signed_out: boolean }>(r)),
  changePassword: (current_password: string, new_password: string) =>
    postJson("/api/auth/password", { current_password, new_password }).then((r) => json<{ changed: boolean }>(r)),
  passkeyLoginOptions: () => fetch("/api/auth/passkey/options", { method: "POST" }).then((r) => json<Ceremony>(r)),
  passkeyLogin: (ceremony: string, credential: unknown) =>
    postJson("/api/auth/passkey", { ceremony, credential }).then((r) => json<{ user: User }>(r)),
  passkeys: () => fetch("/api/passkeys").then((r) => json<Passkey[]>(r)),
  passkeyAddOptions: () => fetch("/api/passkeys/options", { method: "POST" }).then((r) => json<Ceremony>(r)),
  addPasskey: (ceremony: string, credential: unknown, name: string) =>
    postJson("/api/passkeys", { ceremony, credential, name }).then((r) => json<{ id: string; name: string }>(r)),
  removePasskey: (id: string) =>
    fetch(`/api/passkeys/${encodeURIComponent(id)}`, { method: "DELETE" }).then((r) => json<{ removed: string }>(r)),
  devices: () => fetch("/api/devices").then((r) => json<PhoneAccess>(r)),
  startPairing: () => fetch("/api/mobile/pairing", { method: "POST" }).then((r) => json<Pairing>(r)),
  removeDevice: (id: number) => fetch(`/api/devices/${id}`, { method: "DELETE" }).then((r) => json<{ removed: number }>(r)),
  members: () => fetch("/api/users").then((r) => json<Member[]>(r)),
  addMember: (name: string, password: string, is_admin: boolean) =>
    postJson("/api/users", { name, password, is_admin }).then((r) => json<User>(r)),
  resetPassword: (userId: number, new_password: string) =>
    postJson(`/api/users/${userId}/password`, { new_password }).then((r) => json<{ changed: boolean }>(r)),
  info: () => fetch("/api/info").then((r) => json<Info>(r)),
  documents: () => fetch("/api/documents").then((r) => json<VaultDocument[]>(r)),
  deleteDocument: (id: string) =>
    fetch(`/api/documents/${encodeURIComponent(id)}`, { method: "DELETE" }).then((r) => json<{ deleted: string }>(r)),
  updateDocument: (id: string, changes: DocumentChanges) =>
    fetch(`/api/documents/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    }).then((r) => json<VaultDocument>(r)),
  reminders: () => fetch("/api/reminders").then((r) => json<{ items: Reminder[]; settings: ReminderSettings }>(r)),
  markReminder: (doc_id: string, kind: Reminder["kind"], done: boolean) =>
    postJson("/api/reminders/done", { doc_id, kind, done }).then((r) => json<{ items: Reminder[] }>(r)),
  saveReminderSettings: (changes: Partial<ReminderSettings>) =>
    postJson("/api/reminders/settings", changes).then((r) => json<ReminderSettings>(r)),
  upcoming: (days = 90) => fetch(`/api/upcoming?days=${days}`).then((r) => json<Upcoming[]>(r)),
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
  saveSettings: (changes: { llm_model?: string; owner_name?: string }) =>
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
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
    if (res.status === 401) toSignIn();
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

export const formatAmount = (amount: string | null) =>
  amount ? `Rs. ${Number(amount).toLocaleString("en-IN", { maximumFractionDigits: 2 })}` : "";

/** "in 12 days", "tomorrow", "today", "3 days ago" */
export function relativeDays(days: number): string {
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  return days > 0 ? `in ${days} days` : `${-days} days ago`;
}

export function daysUntil(iso: string): number {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((new Date(`${iso}T00:00:00`).getTime() - today.getTime()) / 86_400_000);
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
