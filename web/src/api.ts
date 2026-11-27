export type Conversation = {
  id: string;
  title: string;
  updated_at: string;
  project_id?: string | null;
  model?: string | null;
  thinking?: ThinkingLevel | null;
  claude_context?: boolean; // a Claude chat also sends matching memories, documents and notes (opt-in)
  message_count?: number;
};
export const THINKING_LEVELS = ["off", "low", "medium", "high"] as const;
export type ThinkingLevel = (typeof THINKING_LEVELS)[number];
export type Source = {
  handle: string;
  kind: "memory" | "document" | "conversation" | "note" | "project";
  id: string;
  title: string | null;
  snippet: string;
  page: number | null;
  conversation_id: string | null;
  document_id: string | null;
  at: string | null;
  pinned: boolean;
};
export type TraceDecision = "used" | "trimmed" | "reranker_dropped" | "ranked_lower";
export type TraceCandidate = {
  kind: Source["kind"];
  id: string;
  label: string | null; // null once the item has been deleted
  decision: TraceDecision;
  handle?: string;
  pinned?: boolean;
  score?: number;
  keyword_rank?: number | null;
  vector_rank?: number | null;
  sim?: number | null;
  boosted?: boolean;
};
export type RetrievalTrace = {
  v: number;
  query: string;
  vector_search: boolean;
  cutoffs: { vec_min: number; fts_vec_min: number };
  below_cutoff: { keyword: number; vector: number };
  fused: number;
  rerank: { status: "off" | "ran" | "fell_back" | "no_candidates"; pool?: number; kept?: number };
  candidates: TraceCandidate[];
};
export type Step = { kind: "retrieval" | "memory" | "proposal" | "command" | "notice"; label: string; detail: string | null };
export type Usage = { prompt_tokens?: number | null; completion_tokens?: number | null };
export type Message = {
  id?: string;
  role: "user" | "assistant";
  content: string;
  sources?: Source[] | null;
  steps?: Step[] | null;
  model?: string | null;
  thinking?: string; // reasoning shown while the reply streams; never stored
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  created_at?: string;
  has_trace?: boolean; // a retrieval decision trace is stored (GET /api/messages/{id}/trace)
};
export type Doc = {
  id: string;
  title: string;
  filename: string;
  source_path: string | null;
  folder: string | null;
  kind: "pdf" | "markdown" | "text";
  size_bytes: number;
  updated_at: string;
  chunk_count: number;
  pending: number;
  project_id: string | null;
};
export type FolderResult = { added: number; updated: number; unchanged: number; skipped: { path: string; reason: string }[] };
export type SearchHit = { conversation_id: string; title: string; snippet: string; role: string };
export type MemoryKind = "fact" | "preference" | "person" | "project" | "decision";
export type Memory = {
  id: string;
  text: string;
  kind: MemoryKind;
  origin: "stated" | "inferred";
  status: MemoryStatus;
  pinned: boolean;
  source_conversation_id: string | null;
  updated_at: string;
  project_id: string | null;
  replaces_id?: string | null;
  replace_state?: "suggested" | "applied" | null; // suggested: pal thinks this makes an older memory out of date
  replaces_text?: string | null; // the older memory's text (listings only)
  replaced_by_text?: string | null; // for a replaced memory: what replaced it (listings only)
};
export type MemoryStatus = "proposed" | "confirmed" | "replaced";
export type MemoryPatch = Partial<Pick<Memory, "text" | "kind" | "pinned" | "project_id">>;
export type StreamEvent =
  | { type: "token"; text: string }
  | { type: "thinking"; text: string }
  | { type: "thinking_changed"; thinking: ThinkingLevel | null }
  | { type: "memory_saved"; memory: Memory }
  | { type: "sources"; sources: Source[] }
  | { type: "done"; message_id: string; model?: string; usage?: Usage; has_trace?: boolean }
  | { type: "step"; step: Step }
  | { type: "proposal"; proposal: Proposal }
  | { type: "work_changed" }
  | { type: "model_changed"; model: string | null }
  | { type: "error"; message: string; unsent?: boolean };

export type Project = {
  id: string;
  name: string;
  summary: string;
  status: "active" | "archived";
  updated_at?: string;
  // From the project list only.
  open_tasks?: number;
  overdue?: number;
  next_due?: string | null;
  last_activity?: string;
  card?: string;
};
export type Task = {
  id: string;
  project_id: string | null;
  project: string | null;
  title: string;
  status: "open" | "done";
  stage: TaskStage;
  due_date: string | null;
  done_at: string | null;
};
export type TaskStage = "todo" | "doing";
export type Note = { id: string; project_id: string | null; project: string | null; kind: "note" | "decision"; text: string; created_at: string };
export type Reminder = {
  id: string;
  title: string;
  due_at: string;
  rrule: string | null;
  repeat: string;
  active: boolean;
  paused: boolean;
  project_id: string | null;
  project: string | null;
};
export type Notification = { id: string; reminder_id: string | null; title: string; fired_at: string };
export type ProposalPayload = {
  title: string;
  project_id: string | null;
  project: string | null;
  due_text: string | null;
  due_date: string | null;
  at: string | null;
  rrule: string | null;
  repeat: string;
};
export type Proposal = { id: string; conversation_id: string; action: "create_task" | "set_reminder"; payload: ProposalPayload };
export type ProposalEdits = Partial<Pick<ProposalPayload, "title" | "due_date" | "at" | "rrule" | "project_id">>;

export type Me = {
  authenticated: boolean;
  owner: boolean;
  device: string | null;
  password_login: boolean;
  remote_url: string | null;
};
export type DeviceKind = "phone" | "laptop" | "tablet" | "other";
export type Device = {
  id: string;
  name: string;
  kind: DeviceKind;
  created_at: string;
  last_seen_at: string | null;
  revoked_at: string | null;
  passkeys: number;
};
export type PairingCode = { code: string; name: string; kind: DeviceKind; expires_at: string; url: string | null };
export type ProjectOverview = {
  project: Project;
  counts: {
    open_tasks: number;
    overdue: number;
    done_this_week: number;
    scheduled: number;
    memories: number;
    suggested: number;
    documents: number;
    chats: number;
  };
  tasks: { id: string; title: string; due_date: string | null; created_at: string }[];
  scheduled: { id: string; title: string; due_at: string; rrule: string | null; repeat: string; paused: boolean; task: string | null }[];
  memories: { id: string; text: string; kind: string; status: "proposed" | "confirmed"; pinned: boolean; updated_at: string }[];
  documents: { id: string; title: string; filename: string; kind: string; size_bytes: number; updated_at: string; pending: number }[];
  notes: { id: string; kind: "note" | "decision"; text: string; created_at: string }[];
  chats: { id: string; title: string; updated_at: string; message_count: number }[];
};
export type SessionRow = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  chosen_model: string | null;
  project: string | null;
  message_count: number;
  tokens: number;
  last_model: string | null;
  preview: string | null;
};
export type Pressure = { level: "critical" | "elevated"; kind: "disk" | "memory" | "model" | "encryption"; message: string };
export type Encryption = {
  state: "on" | "off" | "encrypting" | "decrypting" | "suspended" | "waiting" | "unavailable" | "unknown";
  drive: string;
  acknowledged: boolean;
};
export type NeglectedTask = {
  id: string; title: string; due_date: string | null; updated_at: string; project_id: string | null;
  project: string | null; reason: "overdue" | "stale";
};
export type QuietProject = { id: string; name: string; open_tasks: number; last_activity: string };
export type Attention = {
  tasks: NeglectedTask[];
  projects: QuietProject[];
  rules: { stale_days: number; overdue_days: number; quiet_project_days: number };
};
export type DigestRow = { id: string; week_start: string; created_at: string; read_at: string | null };
type DigestTask = { id: string; title: string; due_date: string | null; project: string | null; done_at?: string };
export type DigestBody = {
  period: { start: string; end: string };
  counts: {
    done: number; added: number; reminders_fired: number; memories_added: number;
    documents_added: number; chats: number; messages_sent: number;
  };
  done: DigestTask[];
  due_this_week: DigestTask[];
  overdue: DigestTask[];
  upcoming_reminders: { id: string; title: string; due_at: string; project: string | null }[];
  projects: { name: string; done: number; open: number }[];
  neglected: { tasks: NeglectedTask[]; projects: QuietProject[] };
};
export type Digest = DigestRow & { body: DigestBody };
export type Status = {
  version: string;
  started_at: string;
  uptime_s: number;
  model: string;
  num_ctx: number;
  ollama: { up: boolean; installed: string[]; error: string | null };
  database: { up: boolean };
  counts: {
    conversations: number;
    messages: number;
    active_chats: number;
    memories: number;
    inbox: number;
    documents: number;
    embed_backlog: number;
    stale_pdfs: number;
    reread: RereadJob;
    open_tasks: number;
    reminders: number;
    next_reminder: string | null;
    devices: number;
    tokens_24h: number;
  };
  backups: { count: number; plaintext: number; latest_at: string | null; latest_bytes: number | null; dir?: string };
  disk_free: number | null;
  pressure: Pressure[];
  encryption: Encryption | null;
  recent: SessionRow[];
};
export type ModelList = {
  default: string;
  ollama_up: boolean;
  claude_key: boolean;
  models: { name: string; installed: boolean | null; provider: "local" | "claude" }[];
};
export const isClaude = (m: string | null | undefined) => !!m && m.startsWith("claude-");
export type UsageDay = { day: string; replies: number; prompt_tokens: number; completion_tokens: number };
export type UsageReport = {
  days: number;
  daily: UsageDay[];
  by_model: (Omit<UsageDay, "day"> & { model: string })[];
  totals: { replies: number; prompt_tokens: number; completion_tokens: number; untracked_replies: number };
};
export type LogLine = {
  seq: number;
  at: string;
  level: "debug" | "info" | "warning" | "error" | "critical";
  logger: string;
  message: string;
};
export type SettingField = {
  key: string;
  label: string;
  group: string;
  kind: "model" | "int" | "models" | "choice";
  help: string;
  minimum: number | null;
  maximum: number | null;
  restart: boolean;
  choices: string[] | null;
  value: string | number | string[];
  default: string | number | string[];
  changed: boolean;
};
export type SettingsReport = { fields: SettingField[]; allowed_models: string[]; installed_models: string[] | null };
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- WebAuthn options are opaque JSON, converted in webauthn.ts
type Ceremony = { flow: string; options: Record<string, any> };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Pal-Request": "1", ...(init.headers ?? {}) },
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, typeof detail === "string" ? detail : "Request failed");
  }
  return resp.status === 204 ? (undefined as T) : resp.json();
}

export type RereadJob = {
  running: boolean;
  total?: number;
  done?: number;
  current?: string | null;
  failed?: { path: string; reason: string }[];
  stopped?: boolean;
  started_at?: string;
  finished_at?: string | null;
};
export type DocProcessing = {
  pdf_reader: "docling" | "pypdf";
  indexing: { pending: number; total: number };
  stale_pdfs: { files: number; bytes: number; uploads: number; parser: string };
  reread: RereadJob;
  can_reread: boolean;
};

export const api = {
  attention: () => call<Attention>("/api/attention"),
  stillOnIt: (taskId: string) => call(`/api/tasks/${taskId}/still-on-it`, { method: "POST" }),
  digests: () => call<DigestRow[]>("/api/digests"),
  digest: (id: string) => call<Digest>(`/api/digests/${id}`),
  readDigest: (id: string) => call<DigestRow>(`/api/digests/${id}/read`, { method: "POST" }),
  generateDigest: () => call<DigestRow>("/api/digests/generate", { method: "POST" }),
  ackEncryption: () => call("/api/status/encryption/ack", { method: "POST" }),
  me: () => call<Me>("/api/auth/me"),
  pairStart: (code: string) => call<Ceremony>("/api/auth/pair/start", { method: "POST", body: JSON.stringify({ code }) }),
  pairFinish: (flow: string, credential: object) =>
    call<{ device: string }>("/api/auth/pair/finish", { method: "POST", body: JSON.stringify({ flow, credential }) }),
  passkeyStart: () => call<Ceremony>("/api/auth/passkey/start", { method: "POST" }),
  passkeyFinish: (flow: string, credential: object) =>
    call<{ device: string }>("/api/auth/passkey/finish", { method: "POST", body: JSON.stringify({ flow, credential }) }),
  devices: () => call<Device[]>("/api/devices"),
  newPairingCode: (name: string, kind: DeviceKind) =>
    call<PairingCode>("/api/devices/pairing", { method: "POST", body: JSON.stringify({ name, kind }) }),
  revokeDevice: (id: string) => call(`/api/devices/${id}/revoke`, { method: "POST" }),
  forgetDevice: (id: string) => call<void>(`/api/devices/${id}`, { method: "DELETE" }),
  login: (password: string) => call("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) }),
  logout: () => call("/api/auth/logout", { method: "POST" }),
  list: () => call<Conversation[]>("/api/conversations"),
  create: () => call<Conversation>("/api/conversations", { method: "POST" }),
  get: (id: string) => call<Conversation & { messages: Message[] }>(`/api/conversations/${id}`),
  remove: (id: string) => call<void>(`/api/conversations/${id}`, { method: "DELETE" }),
  trace: (messageId: string) => call<RetrievalTrace>(`/api/messages/${messageId}/trace`),
  memories: (status: MemoryStatus, q = "") =>
    call<Memory[]>(`/api/memories?status=${status}&q=${encodeURIComponent(q)}`),
  inboxCount: () => call<{ count: number }>("/api/memories/inbox-count"),
  addMemory: (m: { text: string; kind: MemoryKind; pinned: boolean; project_id: string | null }) =>
    call<Memory>("/api/memories", { method: "POST", body: JSON.stringify(m) }),
  editMemory: (id: string, patch: MemoryPatch) =>
    call<Memory>(`/api/memories/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  confirmMemory: (id: string) => call<Memory>(`/api/memories/${id}/confirm`, { method: "POST" }),
  deleteMemory: (id: string) => call<void>(`/api/memories/${id}`, { method: "DELETE" }),
  replaceMemory: (id: string, oldId: string) =>
    call<Memory>(`/api/memories/${id}/replace`, { method: "POST", body: JSON.stringify({ old_id: oldId }) }),
  replaceMemoryWith: (oldId: string, text: string) =>
    call<Memory>(`/api/memories/${oldId}/replace-with`, { method: "POST", body: JSON.stringify({ text }) }),
  restoreMemory: (id: string) => call<Memory>(`/api/memories/${id}/restore`, { method: "POST" }),
  keepBothMemories: (id: string) => call<Memory>(`/api/memories/${id}/keep-both`, { method: "POST" }),
  suggestMemories: (convId: string) =>
    call<{ proposed: Memory[] }>(`/api/conversations/${convId}/suggest-memories`, { method: "POST" }),
  documents: () => call<Doc[]>("/api/documents"),
  docProcessing: () => call<DocProcessing>("/api/documents/processing"),
  startReread: () => call<RereadJob>("/api/documents/reread", { method: "POST" }),
  stopReread: () => call<RereadJob>("/api/documents/reread/stop", { method: "POST" }),
  uploadDocument: (file: File, projectId: string | null = null) =>
    call<{ document: Doc; outcome: string }>(
      `/api/documents/upload?filename=${encodeURIComponent(file.name)}${projectId ? `&project_id=${projectId}` : ""}`,
      { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" } },
    ),
  addFolder: (path: string, project_id: string | null = null) =>
    call<FolderResult>("/api/documents/folder", { method: "POST", body: JSON.stringify({ path, project_id }) }),
  pickFolder: () => call<{ path: string | null }>("/api/documents/folder/pick", { method: "POST" }),
  removeFolder: (path: string) =>
    call<{ removed: number }>("/api/documents/folder/remove", { method: "POST", body: JSON.stringify({ path }) }),
  deleteDocument: (id: string) => call<void>(`/api/documents/${id}`, { method: "DELETE" }),
  setDocumentProject: (id: string, project_id: string | null) =>
    call<Doc>(`/api/documents/${id}/project`, { method: "PUT", body: JSON.stringify({ project_id }) }),
  setFolderProject: (path: string, project_id: string | null) =>
    call<{ updated: number }>("/api/documents/folder/project", { method: "POST", body: JSON.stringify({ path, project_id }) }),
  projects: (archived = false) => call<Project[]>(`/api/projects?archived=${archived}`),
  project: (id: string) => call<Project>(`/api/projects/${id}`),
  addProject: (name: string, summary = "") => call<Project>("/api/projects", { method: "POST", body: JSON.stringify({ name, summary }) }),
  editProject: (id: string, patch: Partial<Pick<Project, "name" | "summary" | "status">>) =>
    call<Project>(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteProject: (id: string) => call<void>(`/api/projects/${id}`, { method: "DELETE" }),
  tasks: (opts: { project_id?: string; status?: "open" | "done" | "all"; no_project?: boolean } = {}) => {
    const q = new URLSearchParams();
    if (opts.project_id) q.set("project_id", opts.project_id);
    if (opts.status) q.set("status", opts.status);
    if (opts.no_project) q.set("no_project", "true");
    return call<Task[]>(`/api/tasks?${q}`);
  },
  addTask: (t: { title: string; project_id?: string | null; due_date?: string | null }) =>
    call<Task>("/api/tasks", { method: "POST", body: JSON.stringify(t) }),
  editTask: (id: string, patch: Partial<Pick<Task, "title" | "status" | "stage" | "due_date" | "project_id">>) =>
    call<Task>(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteTask: (id: string) => call<void>(`/api/tasks/${id}`, { method: "DELETE" }),
  notes: (project_id?: string) => call<Note[]>(`/api/notes${project_id ? `?project_id=${project_id}` : ""}`),
  addNote: (n: { text: string; kind: "note" | "decision"; project_id?: string | null }) =>
    call<Note>("/api/notes", { method: "POST", body: JSON.stringify(n) }),
  deleteNote: (id: string) => call<void>(`/api/notes/${id}`, { method: "DELETE" }),
  reminders: () => call<Reminder[]>("/api/reminders"),
  addReminder: (r: { title: string; at: string; rrule?: string | null; project_id?: string | null }) =>
    call<Reminder>("/api/reminders", { method: "POST", body: JSON.stringify(r) }),
  deleteReminder: (id: string) => call<void>(`/api/reminders/${id}`, { method: "DELETE" }),
  notifications: () => call<Notification[]>("/api/notifications"),
  dismiss: (id: string) => call(`/api/notifications/${id}/dismiss`, { method: "POST" }),
  dismissAll: () => call("/api/notifications/dismiss-all", { method: "POST" }),
  snooze: (id: string, minutes: number) =>
    call<Reminder>(`/api/notifications/${id}/snooze`, { method: "POST", body: JSON.stringify({ minutes }) }),
  proposals: (convId: string) => call<Proposal[]>(`/api/conversations/${convId}/proposals`),
  confirmProposal: (id: string, edits: ProposalEdits) =>
    call<{ task?: Task; reminder?: Reminder }>(`/api/proposals/${id}/confirm`, { method: "POST", body: JSON.stringify(edits) }),
  rejectProposal: (id: string) => call<void>(`/api/proposals/${id}`, { method: "DELETE" }),
  setFocus: (convId: string, project_id: string | null) =>
    call(`/api/conversations/${convId}/project`, { method: "PUT", body: JSON.stringify({ project_id }) }),
  search: (q: string) => call<SearchHit[]>(`/api/search?q=${encodeURIComponent(q)}`),
  projectOverview: (id: string) => call<ProjectOverview>(`/api/projects/${id}/overview`),
  status: () => call<Status>("/api/status"),
  models: () => call<ModelList>("/api/models"),
  sessions: (q = "") => call<SessionRow[]>(`/api/sessions?q=${encodeURIComponent(q)}`),
  patchConversation: (id: string, patch: { title?: string; model?: string | null; thinking?: ThinkingLevel | null; claude_context?: boolean }) =>
    call<Conversation>(`/api/conversations/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  bulkDelete: (ids: string[]) =>
    call<{ removed: number }>("/api/conversations/bulk-delete", { method: "POST", body: JSON.stringify({ ids }) }),
  exportUrl: (id: string, format: "md" | "json") => `/api/conversations/${id}/export?format=${format}`,
  usage: (days: number) => call<UsageReport>(`/api/analytics/usage?days=${days}`),
  logs: (level: string, q: string) => call<LogLine[]>(`/api/logs?level=${level}&q=${encodeURIComponent(q)}`),
  settings: () => call<SettingsReport>("/api/settings"),
  saveSettings: (changes: Record<string, unknown>) =>
    call<SettingsReport>("/api/settings", { method: "PUT", body: JSON.stringify(changes) }),
  resetSetting: (key: string) => call<SettingsReport>(`/api/settings/${key}`, { method: "DELETE" }),
  allReminders: () => call<Reminder[]>("/api/reminders?include_done=true"),
  pauseReminder: (id: string) => call<Reminder>(`/api/reminders/${id}/pause`, { method: "POST" }),
  resumeReminder: (id: string) => call<Reminder>(`/api/reminders/${id}/resume`, { method: "POST" }),
  triggerReminder: (id: string) => call<Notification>(`/api/reminders/${id}/trigger`, { method: "POST" }),
};

export async function* streamReply(convId: string, content: string): AsyncGenerator<StreamEvent> {
  const resp = await fetch(`/api/conversations/${convId}/messages`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Pal-Request": "1" },
    body: JSON.stringify({ content }),
  });
  if (!resp.ok || !resp.body) throw new ApiError(resp.status, "Could not send message");
  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) if (line.trim()) yield JSON.parse(line) as StreamEvent;
  }
}
