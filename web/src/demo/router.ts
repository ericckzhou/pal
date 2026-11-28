// Answers every /api request the web app makes, from in-memory demo state.
import type { Conversation, Device, Memory, Notification, Project, Reminder, Task } from "../api";
import { replyStream } from "./chat";
import { DemoState, MIN, newId, seed } from "./data";
import * as v from "./views";

export class DemoError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- request bodies of every demo route
type Ctx = { m: string[]; q: URLSearchParams; body: any };
type Handler = (c: Ctx) => unknown;

const OFFLINE = "Not available in the demo: pal reads files on the PC it runs on.";
const state: DemoState = seed();
let digestRead = false;
// Starts empty; "Run now" on the Schedule page fires one.
let notifications: Notification[] = [];

const find = <T extends { id: string }>(list: T[], id: string, what: string): T => {
  const item = list.find((x) => x.id === id);
  if (!item) throw new DemoError(404, `${what} not found`);
  return item;
};
const projectName = (id: string | null | undefined) => (id ? state.projects.find((p) => p.id === id)?.name ?? null : null);
const conv = (id: string) => find(state.conversations, id, "Conversation");
const listConversations = (): Conversation[] =>
  [...state.conversations]
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .map(({ messages, created_at: _c, ...c }) => ({ ...c, message_count: messages.length }));

function patchTask(t: Task, p: Partial<Task>): Task {
  const next = { ...t, ...p };
  if (p.project_id !== undefined) next.project = projectName(p.project_id);
  if (p.status === "done") next.done_at = new Date().toISOString();
  if (p.status === "open") next.done_at = null;
  state.tasks = state.tasks.map((x) => (x.id === t.id ? next : x));
  return next;
}

function addTask(b: { title: string; project_id?: string | null; due_date?: string | null }): Task {
  const t: Task = {
    id: newId(), title: b.title, project_id: b.project_id ?? null, project: projectName(b.project_id),
    status: "open", stage: "todo", due_date: b.due_date ?? null, done_at: null,
  };
  state.tasks = [t, ...state.tasks];
  return t;
}

function addReminder(b: { title: string; at: string; rrule?: string | null; project_id?: string | null }): Reminder {
  const r: Reminder = {
    id: newId(), title: b.title, due_at: b.at, rrule: b.rrule ?? null, repeat: b.rrule ? "Repeats" : "",
    active: true, paused: false, project_id: b.project_id ?? null, project: projectName(b.project_id),
  };
  state.reminders = [...state.reminders, r];
  return r;
}

const setReminder = (id: string, p: Partial<Reminder>) => {
  const r = { ...find(state.reminders, id, "Reminder"), ...p };
  state.reminders = state.reminders.map((x) => (x.id === id ? r : x));
  return r;
};
const setMemory = (id: string, p: Partial<Memory>) => {
  const m = { ...find(state.memories, id, "Memory"), ...p, updated_at: new Date().toISOString() };
  state.memories = state.memories.map((x) => (x.id === id ? m : x));
  return m;
};
const setProject = (id: string, p: Partial<Project>) => {
  const next = { ...find(state.projects, id, "Project"), ...p, updated_at: new Date().toISOString() };
  state.projects = state.projects.map((x) => (x.id === id ? next : x));
  return next;
};
const setDevice = (id: string, p: Partial<Device>) => {
  const d = { ...find(state.devices, id, "Device"), ...p };
  state.devices = state.devices.map((x) => (x.id === id ? d : x));
  return d;
};

function saveSettings(changes: Record<string, unknown>) {
  state.settings = state.settings.map((f) =>
    f.key in changes ? { ...f, value: changes[f.key] as typeof f.value, changed: changes[f.key] !== f.default } : f,
  );
  return settingsReport();
}
const settingsReport = () => ({
  fields: state.settings, allowed_models: ["qwen3.5:4b", "llama3.2:3b"], installed_models: ["qwen3.5:4b", "llama3.2:3b", "nomic-embed-text"],
});

// Made-up connections: nothing reaches Google from the demo.
const demoConnections = {
  google_client: true,
  client_command: "pal google-client --set client.json",
  services: (["calendar", "drive", "gmail"] as const).map((service) => ({
    provider: "google", service, command: `pal connect google ${service}`,
    grants: { calendar: "read your Google Calendar events (read-only)",
              drive: "read every file in your Google Drive (read-only; pal only syncs the folders you pick)",
              gmail: "read your Gmail (read-only; pal searches it only when you ask and never stores mail)" }[service],
  })),
  connections: [{
    id: "demo-conn-drive", provider: "google", service: "drive", account: "you@example.com", scopes: [], enabled: true,
    project_id: null, config: { folders: [{ id: "demo-folder", name: "Notes" }] }, last_sync_at: new Date().toISOString(),
    last_ok_at: new Date().toISOString(), last_error: null, created_at: new Date().toISOString(), items: 12,
  }],
  // StarcallOS runs on the visitor's own PC, so the demo shows it not set up.
  starcall: {
    native: true, reason: null, meaning_search: true, connection: null, runtime: null,
    scopes: [
      { name: "passages", label: "Passages", what: "Search the indexed text of your StarcallOS sources. Always on: it's what chats look things up in." },
      { name: "concepts", label: "Concepts", what: "Also match a concept by name when its whole name is in your question (its name and definition)." },
    ],
  },
};

const routes: [string, RegExp, Handler][] = [
  // Auth: the demo is always "the owner at the PC".
  ["GET", /^auth\/me$/, () => ({ authenticated: true, owner: true, device: null, password_login: true, remote_url: "https://demo-pc.example.ts.net" })],
  ["POST", /^auth\/(login|logout)$/, () => ({ ok: true })],
  ["POST", /^auth\/(pair|passkey)\//, () => { throw new DemoError(403, "Passkeys are not available in the demo."); }],

  // Status, reports, logs, settings.
  ["GET", /^status$/, () => v.status(state)],
  ["POST", /^status\/encryption\/ack$/, () => ({ ok: true })],
  ["GET", /^models$/, () => v.models()],
  ["GET", /^analytics\/usage$/, ({ q }) => v.usage(Number(q.get("days") ?? 30))],
  ["GET", /^logs$/, ({ q }) => v.logs(q.get("level") ?? "info", q.get("q") ?? "")],
  ["GET", /^settings$/, () => settingsReport()],
  ["PUT", /^settings$/, ({ body }) => saveSettings(body)],
  ["DELETE", /^settings\/([\w-]+)$/, ({ m }) => saveSettings({ [m[1]]: state.settings.find((f) => f.key === m[1])?.default })],
  ["GET", /^attention$/, () => v.attention(state)],
  ["GET", /^digests$/, () => v.digestRows(digestRead)],
  ["POST", /^digests\/generate$/, () => v.digestRows(digestRead)[0]],
  ["GET", /^digests\/([\w-]+)$/, ({ m }) => v.digest(state, find(v.digestRows(digestRead), m[1], "Digest"))],
  ["POST", /^digests\/([\w-]+)\/read$/, ({ m }) => { digestRead = true; return find(v.digestRows(true), m[1], "Digest"); }],

  // Conversations and chat.
  ["GET", /^conversations$/, () => listConversations()],
  ["POST", /^conversations$/, () => {
    const c = { id: newId(), title: "New chat", updated_at: new Date().toISOString(), created_at: new Date().toISOString(), project_id: null, model: null, thinking: null, messages: [] };
    state.conversations = [c, ...state.conversations];
    return c;
  }],
  ["POST", /^conversations\/bulk-delete$/, ({ body }) => {
    const ids = new Set<string>(body.ids);
    state.conversations = state.conversations.filter((c) => !ids.has(c.id));
    return { removed: ids.size };
  }],
  ["GET", /^conversations\/([\w-]+)$/, ({ m }) => { const c = conv(m[1]); return { ...c, messages: [...c.messages] }; }],
  ["PATCH", /^conversations\/([\w-]+)$/, ({ m, body }) => {
    if (typeof body.model === "string" && body.model.startsWith("claude-"))
      throw new DemoError(400, "Claude needs an API key on the PC. The demo runs local models only.");
    return Object.assign(conv(m[1]), body);
  }],
  ["DELETE", /^conversations\/([\w-]+)$/, ({ m }) => { state.conversations = state.conversations.filter((c) => c.id !== m[1]); }],
  ["PUT", /^conversations\/([\w-]+)\/project$/, ({ m, body }) => { conv(m[1]).project_id = body.project_id; return { ok: true }; }],
  ["GET", /^conversations\/([\w-]+)\/proposals$/, ({ m }) => state.proposals.filter((p) => p.conversation_id === m[1])],
  ["POST", /^conversations\/([\w-]+)\/suggest-memories$/, ({ m }) => {
    const mem: Memory = { id: newId(), text: "Likes to plan the week on Sunday evenings.", kind: "preference", origin: "inferred", status: "proposed", pinned: false, source_conversation_id: m[1], updated_at: new Date().toISOString(), project_id: null };
    state.memories = [mem, ...state.memories];
    return { proposed: [mem] };
  }],
  ["GET", /^sessions$/, ({ q }) => v.sessions(state, q.get("q") ?? "")],
  ["GET", /^search$/, ({ q }) => {
    const needle = (q.get("q") ?? "").toLowerCase();
    return state.conversations.flatMap((c) =>
      c.messages.filter((x) => needle && x.content.toLowerCase().includes(needle)).slice(0, 1)
        .map((x) => ({ conversation_id: c.id, title: c.title, snippet: x.content.slice(0, 140), role: x.role })),
    );
  }],

  // Proposals drafted in chat.
  ["POST", /^proposals\/([\w-]+)\/confirm$/, ({ m, body }) => {
    const p = find(state.proposals, m[1], "Proposal");
    state.proposals = state.proposals.filter((x) => x.id !== p.id);
    const pl = { ...p.payload, ...body };
    return p.action === "create_task"
      ? { task: addTask({ title: pl.title, project_id: pl.project_id, due_date: pl.due_date }) }
      : { reminder: addReminder({ title: pl.title, at: pl.at ?? new Date().toISOString(), rrule: pl.rrule, project_id: pl.project_id }) };
  }],
  ["DELETE", /^proposals\/([\w-]+)$/, ({ m }) => { state.proposals = state.proposals.filter((x) => x.id !== m[1]); }],

  // Memory.
  ["GET", /^memories$/, ({ q }) => {
    const needle = (q.get("q") ?? "").toLowerCase();
    const text = (id?: string | null) => state.memories.find((x) => x.id === id);
    return state.memories
      .filter((x) => x.status === q.get("status") && (!needle || x.text.toLowerCase().includes(needle)))
      .map((x) => {
        const old = text(x.replaces_id);
        const shown = old && (x.replace_state === "applied" || old.status === "confirmed");
        const by = state.memories.find((n) => n.replaces_id === x.id && n.replace_state === "applied");
        return { ...x, replaces_text: shown ? old.text : null, replaced_by_text: by?.text ?? null };
      });
  }],
  ["POST", /^memories\/([\w-]+)\/replace$/, ({ m, body }) => {
    setMemory(body.old_id, { status: "replaced" });
    return setMemory(m[1], { status: "confirmed", replaces_id: body.old_id, replace_state: "applied" });
  }],
  ["POST", /^memories\/([\w-]+)\/replace-with$/, ({ m, body }) => {
    const old = state.memories.find((x) => x.id === m[1]);
    if (!old) throw new DemoError(404, "Memory not found");
    const mem: Memory = {
      ...old, id: newId(), text: body.text, origin: "stated", status: "confirmed", source_conversation_id: null,
      updated_at: new Date().toISOString(), replaces_id: old.id, replace_state: "applied",
    };
    setMemory(old.id, { status: "replaced" });
    state.memories = [mem, ...state.memories];
    return mem;
  }],
  ["POST", /^memories\/([\w-]+)\/restore$/, ({ m }) => {
    state.memories = state.memories.map((x) =>
      x.replaces_id === m[1] && x.replace_state === "applied" ? { ...x, replaces_id: null, replace_state: null } : x);
    return setMemory(m[1], { status: "confirmed" });
  }],
  ["POST", /^memories\/([\w-]+)\/keep-both$/, ({ m }) => setMemory(m[1], { replaces_id: null, replace_state: null })],
  ["GET", /^memories\/inbox-count$/, () => ({ count: state.memories.filter((x) => x.status === "proposed").length })],
  ["POST", /^memories$/, ({ body }) => {
    const mem: Memory = { id: newId(), origin: "stated", status: "confirmed", source_conversation_id: null, updated_at: new Date().toISOString(), ...body };
    state.memories = [mem, ...state.memories];
    return mem;
  }],
  ["PATCH", /^memories\/([\w-]+)$/, ({ m, body }) => setMemory(m[1], body)],
  ["POST", /^memories\/([\w-]+)\/confirm$/, ({ m }) => setMemory(m[1], { status: "confirmed" })],
  ["DELETE", /^memories\/([\w-]+)$/, ({ m }) => { state.memories = state.memories.filter((x) => x.id !== m[1]); }],

  // Documents. Reading files needs the real PC.
  ["GET", /^documents$/, () => state.documents],
  ["GET", /^documents\/processing$/, () => ({
    pdf_reader: "docling", indexing: { pending: 0, total: state.documents.length },
    stale_pdfs: { files: 0, bytes: 0, uploads: 0, parser: "docling" }, reread: { running: false }, can_reread: false,
  })],
  ["POST", /^documents\/(upload|folder|folder\/pick|folder\/remove|reread|reread\/stop)$/, () => { throw new DemoError(403, OFFLINE); }],
  ["POST", /^documents\/folder\/project$/, ({ body }) => {
    const hit = state.documents.filter((d) => d.folder === body.path);
    state.documents = state.documents.map((d) => (d.folder === body.path ? { ...d, project_id: body.project_id } : d));
    return { updated: hit.length };
  }],
  ["PUT", /^documents\/([\w-]+)\/project$/, ({ m, body }) => {
    state.documents = state.documents.map((d) => (d.id === m[1] ? { ...d, project_id: body.project_id } : d));
    return find(state.documents, m[1], "Document");
  }],
  ["DELETE", /^documents\/([\w-]+)$/, ({ m }) => { state.documents = state.documents.filter((d) => d.id !== m[1]); }],

  // Projects, tasks, notes.
  ["GET", /^projects$/, ({ q }) => v.projectList(state, q.get("archived") === "true")],
  ["POST", /^projects$/, ({ body }) => {
    const p: Project = { id: newId(), name: body.name, summary: body.summary ?? "", status: "active", updated_at: new Date().toISOString() };
    state.projects = [p, ...state.projects];
    return p;
  }],
  ["GET", /^projects\/([\w-]+)\/overview$/, ({ m }) => v.overview(state, m[1]) ?? (() => { throw new DemoError(404, "Project not found"); })()],
  ["GET", /^projects\/([\w-]+)$/, ({ m }) => find(state.projects, m[1], "Project")],
  ["PATCH", /^projects\/([\w-]+)$/, ({ m, body }) => setProject(m[1], body)],
  ["DELETE", /^projects\/([\w-]+)$/, ({ m }) => { state.projects = state.projects.filter((p) => p.id !== m[1]); }],
  ["GET", /^tasks$/, ({ q }) => {
    const status = q.get("status") ?? "open";
    return state.tasks.filter((t) =>
      (status === "all" || t.status === status) &&
      (!q.get("project_id") || t.project_id === q.get("project_id")) &&
      (q.get("no_project") !== "true" || !t.project_id));
  }],
  ["POST", /^tasks$/, ({ body }) => addTask(body)],
  ["PATCH", /^tasks\/([\w-]+)$/, ({ m, body }) => patchTask(find(state.tasks, m[1], "Task"), body)],
  ["POST", /^tasks\/([\w-]+)\/still-on-it$/, ({ m }) => patchTask(find(state.tasks, m[1], "Task"), {})],
  ["DELETE", /^tasks\/([\w-]+)$/, ({ m }) => { state.tasks = state.tasks.filter((t) => t.id !== m[1]); }],
  ["GET", /^notes$/, ({ q }) => state.notes.filter((n) => !q.get("project_id") || n.project_id === q.get("project_id"))],
  ["POST", /^notes$/, ({ body }) => {
    const n = { id: newId(), project_id: body.project_id ?? null, project: projectName(body.project_id), kind: body.kind, text: body.text, created_at: new Date().toISOString() };
    state.notes = [n, ...state.notes];
    return n;
  }],
  ["DELETE", /^notes\/([\w-]+)$/, ({ m }) => { state.notes = state.notes.filter((n) => n.id !== m[1]); }],

  // Reminders and notifications.
  ["GET", /^reminders$/, ({ q }) => (q.get("include_done") === "true" ? state.reminders : state.reminders.filter((r) => r.active))],
  ["POST", /^reminders$/, ({ body }) => addReminder(body)],
  ["DELETE", /^reminders\/([\w-]+)$/, ({ m }) => { state.reminders = state.reminders.filter((r) => r.id !== m[1]); }],
  ["POST", /^reminders\/([\w-]+)\/pause$/, ({ m }) => setReminder(m[1], { paused: true })],
  ["POST", /^reminders\/([\w-]+)\/resume$/, ({ m }) => setReminder(m[1], { paused: false })],
  ["POST", /^reminders\/([\w-]+)\/trigger$/, ({ m }) => {
    const n = { id: newId(), reminder_id: m[1], title: find(state.reminders, m[1], "Reminder").title, fired_at: new Date().toISOString() };
    notifications = [n, ...notifications];
    return n;
  }],
  ["GET", /^notifications$/, () => notifications],
  ["POST", /^notifications\/dismiss-all$/, () => { notifications = []; return { ok: true }; }],
  ["POST", /^notifications\/([\w-]+)\/dismiss$/, ({ m }) => { notifications = notifications.filter((n) => n.id !== m[1]); return { ok: true }; }],
  ["POST", /^notifications\/([\w-]+)\/snooze$/, ({ m, body }) => {
    const n = find(notifications, m[1], "Notification");
    notifications = notifications.filter((x) => x.id !== n.id);
    return n.reminder_id ? setReminder(n.reminder_id, { due_at: new Date(Date.now() + body.minutes * MIN).toISOString() }) : {};
  }],

  // Devices.
  ["GET", /^connections$/, () => demoConnections],
  ["POST", /^connections\//, () => { throw new DemoError(403, "Google isn't connected in the demo."); }],
  ["PATCH", /^connections\//, () => { throw new DemoError(403, "Google isn't connected in the demo."); }],
  ["DELETE", /^connections\//, () => { throw new DemoError(403, "Google isn't connected in the demo."); }],
  ["PUT", /^connections\/starcall$/, () => { throw new DemoError(403, "StarcallOS can't be set up in the demo."); }],
  ["GET", /^devices$/, () => state.devices],
  ["POST", /^devices\/pairing$/, ({ body }) => ({
    code: "DEMO-7QX2", name: body.name, kind: body.kind, expires_at: new Date(Date.now() + 10 * MIN).toISOString(),
    url: "https://demo-pc.example.ts.net/#pair=DEMO-7QX2",
  })],
  ["POST", /^devices\/([\w-]+)\/revoke$/, ({ m }) => setDevice(m[1], { revoked_at: new Date().toISOString() })],
  ["DELETE", /^devices\/([\w-]+)$/, ({ m }) => { state.devices = state.devices.filter((d) => d.id !== m[1]); }],
];

const json = (status: number, data: unknown) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

/** Builds the Response the real server would send for this /api request. */
export async function handle(method: string, url: URL, rawBody: BodyInit | null | undefined): Promise<Response> {
  const path = url.pathname.replace(/^\/api\//, "");
  await new Promise((r) => setTimeout(r, 60 + Math.random() * 120)); // feel like a network call

  const msg = path.match(/^conversations\/([\w-]+)\/messages$/);
  if (method === "POST" && msg) {
    const { content } = JSON.parse(String(rawBody ?? "{}"));
    return new Response(replyStream(state, conv(msg[1]), content), { headers: { "Content-Type": "application/x-ndjson" } });
  }

  const route = routes.find(([m, re]) => m === method && re.test(path));
  if (!route) return json(404, { detail: "Not available in the demo." });
  try {
    const body = typeof rawBody === "string" && rawBody ? JSON.parse(rawBody) : rawBody ?? null;
    const result = route[2]({ m: path.match(route[1])!, q: url.searchParams, body });
    return result === undefined ? new Response(null, { status: 204 }) : json(200, result);
  } catch (err) {
    if (err instanceof DemoError) return json(err.status, { detail: err.message });
    console.error("demo route failed", method, path, err);
    return json(500, { detail: "Demo error" });
  }
}
