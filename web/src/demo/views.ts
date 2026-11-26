// Read-only reports the real server computes from the database, rebuilt from demo state.
import type {
  Attention, Digest, DigestRow, LogLine, ModelList, ProjectOverview, Project, SessionRow, Status, UsageReport,
} from "../api";
import { ago, dateIn, DAY, DemoState, MIN, MODEL } from "./data";

const STARTED = Date.now() - (3 * DAY + 4 * 3600_000);
const today = () => dateIn(0);

export const openTasks = (s: DemoState) => s.tasks.filter((t) => t.status === "open");

function tokens(c: DemoState["conversations"][number]) {
  return c.messages.reduce((n, m) => n + (m.prompt_tokens ?? 0) + (m.completion_tokens ?? 0), 0);
}

export function sessions(s: DemoState, q = ""): SessionRow[] {
  const needle = q.trim().toLowerCase();
  return [...s.conversations]
    .filter((c) => !needle || c.title.toLowerCase().includes(needle) || c.messages.some((m) => m.content.toLowerCase().includes(needle)))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .map((c) => {
      const last = [...c.messages].reverse().find((m) => m.role === "assistant");
      return {
        id: c.id, title: c.title, created_at: c.created_at, updated_at: c.updated_at, chosen_model: c.model ?? null,
        project: s.projects.find((p) => p.id === c.project_id)?.name ?? null, message_count: c.messages.length,
        tokens: tokens(c), last_model: last?.model ?? null, preview: c.messages[0]?.content.slice(0, 120) ?? null,
      };
    });
}

/** Like the server: archived=true means "include archived", not "only archived". */
export function projectList(s: DemoState, includeArchived: boolean): Project[] {
  return s.projects
    .filter((p) => includeArchived || p.status === "active")
    .map((p) => {
      const open = openTasks(s).filter((t) => t.project_id === p.id);
      const dues = open.map((t) => t.due_date).filter((d): d is string => !!d).sort();
      return {
        ...p,
        open_tasks: open.length,
        overdue: dues.filter((d) => d < today()).length,
        next_due: dues.find((d) => d >= today()) ?? null,
        last_activity: p.updated_at ?? ago(DAY),
      };
    })
    .sort((a, b) => (b.last_activity ?? "").localeCompare(a.last_activity ?? ""));
}

export function overview(s: DemoState, id: string): ProjectOverview | null {
  const project = projectList(s, true).find((p) => p.id === id);
  if (!project) return null;
  const tasks = openTasks(s).filter((t) => t.project_id === id);
  const reminders = s.reminders.filter((r) => r.active && r.project_id === id);
  const memories = s.memories.filter((m) => m.project_id === id);
  const docs = s.documents.filter((d) => d.project_id === id);
  const chats = s.conversations.filter((c) => c.project_id === id);
  const weekAgo = new Date(Date.now() - 7 * DAY).toISOString();
  return {
    project,
    counts: {
      open_tasks: tasks.length,
      overdue: tasks.filter((t) => t.due_date && t.due_date < today()).length,
      done_this_week: s.tasks.filter((t) => t.project_id === id && t.done_at && t.done_at > weekAgo).length,
      scheduled: reminders.length,
      memories: memories.filter((m) => m.status === "confirmed").length,
      suggested: memories.filter((m) => m.status === "proposed").length,
      documents: docs.length,
      chats: chats.length,
    },
    tasks: tasks.map((t) => ({ id: t.id, title: t.title, due_date: t.due_date, created_at: ago(7 * DAY) })),
    scheduled: reminders.map((r) => ({ id: r.id, title: r.title, due_at: r.due_at, rrule: r.rrule, repeat: r.repeat, paused: r.paused, task: null })),
    memories: [...memories]
      .sort((a, b) => (a.status === b.status ? 0 : a.status === "proposed" ? -1 : 1))
      .map((m) => ({ id: m.id, text: m.text, kind: m.kind, status: m.status, pinned: m.pinned, updated_at: m.updated_at })),
    documents: docs.map((d) => ({ id: d.id, title: d.title, filename: d.filename, kind: d.kind, size_bytes: d.size_bytes, updated_at: d.updated_at, pending: d.pending })),
    notes: s.notes.filter((n) => n.project_id === id).map((n) => ({ id: n.id, kind: n.kind, text: n.text, created_at: n.created_at })),
    chats: chats.map((c) => ({ id: c.id, title: c.title, updated_at: c.updated_at, message_count: c.messages.length })),
  };
}

export function attention(s: DemoState): Attention {
  const overdueCutoff = dateIn(-7);
  return {
    tasks: openTasks(s)
      .filter((t) => t.due_date && t.due_date <= overdueCutoff)
      .map((t) => ({ id: t.id, title: t.title, due_date: t.due_date, updated_at: ago(12 * DAY), project_id: t.project_id, project: t.project, reason: "overdue" as const })),
    projects: projectList(s, false)
      .filter((p) => p.id === "p-spanish")
      .map((p) => ({ id: p.id, name: p.name, open_tasks: p.open_tasks ?? 0, last_activity: p.last_activity ?? ago(26 * DAY) })),
    rules: { stale_days: 14, overdue_days: 7, quiet_project_days: 21 },
  };
}

export function status(s: DemoState): Status {
  const proposed = s.memories.filter((m) => m.status === "proposed").length;
  const upcoming = s.reminders.filter((r) => r.active && !r.paused).map((r) => r.due_at).sort();
  return {
    version: "0.1.0",
    started_at: new Date(STARTED).toISOString(),
    uptime_s: Math.round((Date.now() - STARTED) / 1000),
    model: MODEL,
    num_ctx: 16384,
    ollama: { up: true, installed: [MODEL, "llama3.2:3b", "nomic-embed-text"], error: null },
    database: { up: true },
    counts: {
      conversations: s.conversations.length,
      messages: s.conversations.reduce((n, c) => n + c.messages.length, 0),
      active_chats: 1,
      memories: s.memories.length - proposed,
      inbox: proposed,
      documents: s.documents.length,
      embed_backlog: 0,
      stale_pdfs: 0,
      reread: { running: false },
      open_tasks: openTasks(s).length,
      reminders: s.reminders.filter((r) => r.active).length,
      next_reminder: upcoming[0] ?? null,
      devices: s.devices.filter((d) => !d.revoked_at).length,
      tokens_24h: 18_420,
    },
    backups: { count: 14, plaintext: 0, latest_at: ago(9 * 3600_000), latest_bytes: 48_300_000, dir: "C:\\Users\\demo\\AppData\\Local\\pal\\backups" },
    disk_free: 212_000_000_000,
    pressure: [],
    encryption: { state: "on", drive: "C:", acknowledged: true },
    recent: sessions(s).slice(0, 5),
  };
}

export function models(): ModelList {
  return {
    default: MODEL,
    ollama_up: true,
    claude_key: false,
    models: [
      { name: MODEL, installed: true, provider: "local" },
      { name: "llama3.2:3b", installed: true, provider: "local" },
      { name: "claude-sonnet-5-5", installed: null, provider: "claude" },
      { name: "claude-opus-5-5", installed: null, provider: "claude" },
    ],
  };
}

// Deterministic "random" so the charts look the same on every load.
function wobble(i: number) {
  const x = Math.sin(i * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

export function usage(days: number): UsageReport {
  const daily = Array.from({ length: days }, (_, k) => {
    const i = days - 1 - k;
    const weekend = new Date(Date.now() - i * DAY).getDay() % 6 === 0;
    const replies = Math.round((weekend ? 6 : 14) + wobble(i) * 18);
    return {
      day: dateIn(-i),
      replies,
      prompt_tokens: replies * Math.round(1400 + wobble(i + 99) * 900),
      completion_tokens: replies * Math.round(110 + wobble(i + 7) * 90),
    };
  });
  const sum = (k: "replies" | "prompt_tokens" | "completion_tokens") => daily.reduce((n, d) => n + d[k], 0);
  const share = (k: "replies" | "prompt_tokens" | "completion_tokens", f: number) => Math.round(sum(k) * f);
  return {
    days,
    daily,
    by_model: [
      { model: MODEL, replies: share("replies", 0.86), prompt_tokens: share("prompt_tokens", 0.84), completion_tokens: share("completion_tokens", 0.85) },
      { model: "llama3.2:3b", replies: share("replies", 0.14), prompt_tokens: share("prompt_tokens", 0.16), completion_tokens: share("completion_tokens", 0.15) },
    ],
    totals: { replies: sum("replies"), prompt_tokens: sum("prompt_tokens"), completion_tokens: sum("completion_tokens"), untracked_replies: 0 },
  };
}

const LOG_SEED: [number, LogLine["level"], string, string][] = [
  [3 * DAY, "info", "pal.app", "pal 0.1.0 starting; migrations up to date"],
  [3 * DAY, "info", "pal.egress", "outbound guard on: allowed hosts = 127.0.0.1 (ollama)"],
  [3 * DAY, "info", "pal.tailnet", "serving for demo-pc.example.ts.net"],
  [9 * 3600_000, "info", "pal.backup", "nightly backup written and encrypted (48.3 MB)"],
  [9 * 3600_000, "info", "pal.backup", "pruned 1 backup older than the newest 14"],
  [5 * 3600_000, "info", "pal.ingest", "rescan: 1 file changed in C:\\Users\\demo\\Documents\\Apartment"],
  [5 * 3600_000, "info", "pal.embed", "embedded 3 chunks for moving-checklist.md"],
  [2 * 3600_000, "warning", "pal.devices", "sign-in refused for revoked device (id dev0)"],
  [40 * MIN, "info", "pal.reminders", "fired: Spanish practice (20 min)"],
  [12 * MIN, "debug", "pal.retrieval", "query fused 6 keyword + 8 vector hits; top score 0.81"],
  [3 * MIN, "info", "pal.chat", "reply done in 2.4s (2840 prompt + 96 completion tokens)"],
];

export function logs(level: string, q: string): LogLine[] {
  const order = ["debug", "info", "warning", "error", "critical"];
  const min = Math.max(0, order.indexOf(level));
  const needle = q.toLowerCase();
  return LOG_SEED.map(([age, lvl, logger, message], i) => ({ seq: i + 1, at: ago(age), level: lvl, logger, message }))
    .filter((l) => order.indexOf(l.level) >= min && (!needle || `${l.logger} ${l.message}`.toLowerCase().includes(needle)));
}

export function digestRows(read: boolean): DigestRow[] {
  return [
    { id: "dg2", week_start: dateIn(-7), created_at: ago(2 * DAY), read_at: read ? ago(DAY) : null },
    { id: "dg1", week_start: dateIn(-14), created_at: ago(9 * DAY), read_at: ago(8 * DAY) },
  ];
}

export function digest(s: DemoState, row: DigestRow): Digest {
  const open = openTasks(s);
  const done = s.tasks.filter((t) => t.status === "done");
  return {
    ...row,
    body: {
      period: { start: row.week_start, end: dateIn(-1) },
      counts: { done: done.length, added: 7, reminders_fired: 9, memories_added: 3, documents_added: 2, chats: 6, messages_sent: 41 },
      done: done.map((t) => ({ id: t.id, title: t.title, due_date: t.due_date, project: t.project, done_at: t.done_at ?? undefined })),
      due_this_week: open.filter((t) => t.due_date && t.due_date >= dateIn(0) && t.due_date <= dateIn(6)).map((t) => ({ id: t.id, title: t.title, due_date: t.due_date, project: t.project })),
      overdue: open.filter((t) => t.due_date && t.due_date < dateIn(0)).map((t) => ({ id: t.id, title: t.title, due_date: t.due_date, project: t.project })),
      upcoming_reminders: s.reminders.filter((r) => r.active && !r.paused).map((r) => ({ id: r.id, title: r.title, due_at: r.due_at, project: r.project })),
      projects: projectList(s, false).map((p) => ({ name: p.name, done: done.filter((t) => t.project_id === p.id).length, open: p.open_tasks ?? 0 })),
      neglected: (({ tasks, projects }) => ({ tasks, projects }))(attention(s)),
    },
  };
}
