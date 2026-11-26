// Made-up data for the public demo. Nothing here comes from a real pal database.
import type {
  Conversation, Device, Doc, Memory, Message, Note, Project, Proposal, Reminder, SettingField, Task, ThinkingLevel,
} from "../api";

const MIN = 60_000;
const DAY = 24 * 60 * MIN;
export const now = () => new Date();
export const ago = (ms: number) => new Date(Date.now() - ms).toISOString();
export const ahead = (ms: number) => new Date(Date.now() + ms).toISOString();
/** A local calendar date (YYYY-MM-DD) offset by whole days. */
export const dateIn = (days: number) => {
  const d = new Date(Date.now() + days * DAY);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
/** Today (or a later day) at a local wall-clock time. */
export const atTime = (days: number, hour: number, minute = 0) => {
  const d = new Date(Date.now() + days * DAY);
  d.setHours(hour, minute, 0, 0);
  return d.toISOString();
};
/** A local wall-clock time as a datetime-local value (YYYY-MM-DDTHH:MM), as the server sends drafted reminders. */
export const localAt = (days: number, hour: number, minute = 0) =>
  `${dateIn(days)}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
export { MIN, DAY };

let seq = 1000;
export const newId = () => `demo-${(seq++).toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

export const MODEL = "qwen3.5:4b";

export type DemoConversation = Conversation & { created_at: string; messages: Message[] };

export type DemoState = {
  projects: Project[];
  tasks: Task[];
  notes: Note[];
  reminders: Reminder[];
  memories: Memory[];
  documents: Doc[];
  conversations: DemoConversation[];
  proposals: Proposal[];
  devices: Device[];
  settings: SettingField[];
  defaultThinking: ThinkingLevel;
};

const P = { move: "p-move", marathon: "p-marathon", budget: "p-budget", spanish: "p-spanish" } as const;
const projectName: Record<string, string> = {
  [P.move]: "Apartment move",
  [P.marathon]: "Spring half marathon",
  [P.budget]: "Budget app (side project)",
  [P.spanish]: "Learn Spanish",
};

function task(id: string, title: string, project_id: string | null, due: number | null, stage: Task["stage"] = "todo"): Task {
  return {
    id, title, project_id, project: project_id ? projectName[project_id] : null,
    status: "open", stage, due_date: due === null ? null : dateIn(due), done_at: null,
  };
}

function doneTask(id: string, title: string, project_id: string | null, doneDaysAgo: number): Task {
  return { ...task(id, title, project_id, null), status: "done", done_at: ago(doneDaysAgo * DAY) };
}

function setting(
  key: string, label: string, group: string, kind: SettingField["kind"], help: string,
  value: SettingField["value"], extra: Partial<SettingField> = {},
): SettingField {
  return {
    key, label, group, kind, help, minimum: null, maximum: null, restart: false, choices: null,
    value, default: value, changed: false, ...extra,
  };
}

export function seed(): DemoState {
  const projects: Project[] = [
    { id: P.move, name: projectName[P.move], summary: "Moving to the new place on the 1st. Lease signed; movers booked.", status: "active", updated_at: ago(2 * 60 * MIN) },
    { id: P.marathon, name: projectName[P.marathon], summary: "12-week plan, 4 runs a week. Goal: under 1:55.", status: "active", updated_at: ago(DAY) },
    { id: P.budget, name: projectName[P.budget], summary: "React + FastAPI app that imports bank CSVs and tags spending.", status: "active", updated_at: ago(3 * DAY) },
    { id: P.spanish, name: projectName[P.spanish], summary: "Daily practice; aiming for B1 by summer.", status: "active", updated_at: ago(26 * DAY) },
    { id: "p-taxes", name: "2025 taxes", summary: "Filed and refunded.", status: "archived", updated_at: ago(60 * DAY) },
  ];

  const tasks: Task[] = [
    task("t1", "Book the freight elevator for moving day", P.move, 1),
    task("t2", "Forward mail to the new address", P.move, 3),
    task("t3", "Get renter's insurance quotes", P.move, -2),
    task("t4", "Pack the bookshelf and desk", P.move, 5, "doing"),
    task("t5", "Long run: 14 km easy pace", P.marathon, 2),
    task("t6", "Buy new running shoes", P.marathon, 6),
    task("t7", "Write CSV importer tests", P.budget, null, "doing"),
    task("t8", "Add category rules UI", P.budget, null),
    task("t9", "Finish chapter 6 exercises", P.spanish, -9),
    task("t10", "Call the dentist to reschedule", null, 0),
    doneTask("t11", "Sign the lease", P.move, 4),
    doneTask("t12", "Book movers", P.move, 2),
    doneTask("t13", "Tempo run 6 km", P.marathon, 1),
    doneTask("t14", "Set up the FastAPI project skeleton", P.budget, 5),
  ];

  const notes: Note[] = [
    { id: "n1", project_id: P.move, project: projectName[P.move], kind: "decision", text: "Going with Two Guys Moving: cheapest quote that includes packing materials.", created_at: ago(3 * DAY) },
    { id: "n2", project_id: P.move, project: projectName[P.move], kind: "note", text: "Building manager: elevator bookings need 48 hours' notice.", created_at: ago(2 * DAY) },
    { id: "n3", project_id: P.budget, project: projectName[P.budget], kind: "decision", text: "Store amounts as integer cents; never floats.", created_at: ago(6 * DAY) },
    { id: "n4", project_id: P.marathon, project: projectName[P.marathon], kind: "note", text: "Left knee felt tight after hills. Add a mobility session on Thursdays.", created_at: ago(DAY) },
  ];

  const reminders: Reminder[] = [
    { id: "r1", title: "Weekly long run", due_at: atTime(2, 7, 30), rrule: "FREQ=WEEKLY;BYDAY=SA", repeat: "Every Saturday", active: true, paused: false, project_id: P.marathon, project: projectName[P.marathon] },
    { id: "r2", title: "Call the building manager about the elevator", due_at: atTime(1, 10), rrule: null, repeat: "", active: true, paused: false, project_id: P.move, project: projectName[P.move] },
    { id: "r3", title: "Spanish practice (20 min)", due_at: atTime(0, 20), rrule: "FREQ=DAILY", repeat: "Every day", active: true, paused: false, project_id: P.spanish, project: projectName[P.spanish] },
    { id: "r4", title: "Pay rent", due_at: atTime(6, 9), rrule: "FREQ=MONTHLY;BYMONTHDAY=1", repeat: "Every month on the 1st", active: true, paused: true, project_id: null, project: null },
    { id: "r5", title: "Pick up keys from the leasing office", due_at: ago(4 * DAY), rrule: null, repeat: "", active: false, paused: false, project_id: P.move, project: projectName[P.move] },
  ];

  const mem = (id: string, text: string, kind: Memory["kind"], extra: Partial<Memory> = {}): Memory => ({
    id, text, kind, origin: "stated", status: "confirmed", pinned: false, source_conversation_id: null,
    updated_at: ago(5 * DAY), project_id: null, ...extra,
  });
  const memories: Memory[] = [
    mem("m1", "Prefers short, direct answers with bullet points.", "preference", { pinned: true }),
    mem("m2", "Runs in the mornings before work, usually around 6:30.", "fact"),
    mem("m3", "Moving to the new apartment on the 1st of next month.", "project", { project_id: P.move, updated_at: ago(4 * DAY) }),
    mem("m4", "Budget app stores money as integer cents.", "decision", { project_id: P.budget, origin: "inferred" }),
    mem("m5", "Allergic to penicillin.", "fact", { pinned: true, updated_at: ago(40 * DAY) }),
    mem("m6", "Alex is the building manager at the new place.", "person", { project_id: P.move }),
    mem("m7", "Target half-marathon time is under 1:55.", "project", { project_id: P.marathon, origin: "inferred", status: "proposed", source_conversation_id: "c2", updated_at: ago(DAY) }),
    mem("m8", "Usually studies Spanish in the evening.", "fact", { origin: "inferred", status: "proposed", source_conversation_id: "c4", updated_at: ago(2 * DAY) }),
  ];

  const doc = (id: string, title: string, filename: string, kind: Doc["kind"], size: number, chunks: number, project_id: string | null, folder: string | null, days: number): Doc => ({
    id, title, filename, kind, size_bytes: size, chunk_count: chunks, pending: 0, project_id, folder,
    source_path: folder ? `${folder}\\${filename}` : null, updated_at: ago(days * DAY),
  });
  const home = "C:\\Users\\demo\\Documents";
  const documents: Doc[] = [
    doc("d1", "Residential lease agreement", "lease-agreement.pdf", "pdf", 1_482_000, 41, P.move, `${home}\\Apartment`, 6),
    doc("d2", "Moving checklist", "moving-checklist.md", "markdown", 4_200, 3, P.move, `${home}\\Apartment`, 3),
    doc("d3", "Half marathon 12-week plan", "half-marathon-plan.pdf", "pdf", 312_000, 12, P.marathon, `${home}\\Running`, 20),
    doc("d4", "Budget app design notes", "design-notes.md", "markdown", 9_800, 7, P.budget, `${home}\\Code\\budget-app`, 5),
    doc("d5", "Spanish verb tables", "verb-tables.pdf", "pdf", 2_210_000, 58, P.spanish, null, 30),
    doc("d6", "Health insurance summary", "insurance-summary.pdf", "pdf", 640_000, 18, null, null, 45),
  ];

  const conversations: DemoConversation[] = [
    {
      id: "c1", title: "Renter's insurance and the lease", project_id: P.move, model: MODEL, thinking: null,
      created_at: ago(DAY + 40 * MIN), updated_at: ago(DAY),
      messages: [
        { id: "c1m1", role: "user", content: "Does my lease say anything about renter's insurance?", created_at: ago(DAY + 40 * MIN) },
        {
          id: "c1m2", role: "assistant", model: MODEL, prompt_tokens: 2840, completion_tokens: 96, created_at: ago(DAY + 39 * MIN),
          content: "Yes. Section 14 requires renter's insurance with at least **$100,000** in liability coverage, and you must name the landlord as an interested party [d1]. Proof is due before you get the keys [d1].\n\nYou already have a task for this: *Get renter's insurance quotes* [p1].",
          steps: [{ kind: "retrieval", label: "Searched your documents", detail: "2 passages from 1 document" }],
          sources: [
            { handle: "d1", kind: "document", id: "d1", title: "Residential lease agreement", snippet: "14. INSURANCE. Tenant shall maintain renter's insurance with liability coverage of not less than $100,000 and name Landlord as an interested party. Proof of coverage must be provided prior to delivery of keys.", page: 9, conversation_id: null, document_id: "d1", at: null, pinned: false },
            { handle: "p1", kind: "project", id: P.move, title: "Apartment move", snippet: "Moving to the new place on the 1st. Open tasks: renter's insurance quotes (overdue), elevator booking, forward mail.", page: null, conversation_id: null, document_id: null, at: null, pinned: false },
          ],
        },
      ],
    },
    {
      id: "c2", title: "Training plan for this week", project_id: P.marathon, model: MODEL, thinking: "low",
      created_at: ago(2 * DAY), updated_at: ago(2 * DAY - 5 * MIN),
      messages: [
        { id: "c2m1", role: "user", content: "What does my plan say for week 6? My goal is to finish under 1:55.", created_at: ago(2 * DAY) },
        {
          id: "c2m2", role: "assistant", model: MODEL, prompt_tokens: 1910, completion_tokens: 120, created_at: ago(2 * DAY - MIN),
          content: "Week 6 of your plan [d3]:\n\n| Day | Run |\n|---|---|\n| Tue | 6 km tempo |\n| Thu | 5 km easy + strides |\n| Sat | **14 km long run** |\n| Sun | 4 km recovery |\n\nFor a 1:55 finish your goal pace is about **5:27 /km**. Keep the long run around 6:10 /km.",
          steps: [{ kind: "retrieval", label: "Searched your documents", detail: "1 passage from 1 document" }],
          sources: [
            { handle: "d3", kind: "document", id: "d3", title: "Half marathon 12-week plan", snippet: "WEEK 6 — Tue: 6 km tempo · Thu: 5 km easy + 6×20s strides · Sat: 14 km long run (easy) · Sun: 4 km recovery", page: 4, conversation_id: null, document_id: "d3", at: null, pinned: false },
          ],
        },
      ],
    },
    {
      id: "c3", title: "Integer cents vs decimals", project_id: P.budget, model: MODEL, thinking: null,
      created_at: ago(6 * DAY), updated_at: ago(6 * DAY - 10 * MIN),
      messages: [
        { id: "c3m1", role: "user", content: "Should my budget app store money as floats?", created_at: ago(6 * DAY) },
        {
          id: "c3m2", role: "assistant", model: MODEL, prompt_tokens: 640, completion_tokens: 150, created_at: ago(6 * DAY - MIN),
          content: "No. Floats can't represent most decimal amounts exactly:\n\n```python\n>>> 0.1 + 0.2\n0.30000000000000004\n```\n\nStore **integer cents** (`amount_cents: int`) and format only for display. In Postgres use `bigint`, or `numeric(12,2)` if you prefer decimals.",
          steps: [{ kind: "memory", label: "Saved to memory", detail: "Budget app stores money as integer cents." }],
        },
      ],
    },
    {
      id: "c4", title: "Spanish: ser vs estar", project_id: P.spanish, model: MODEL, thinking: null,
      created_at: ago(9 * DAY), updated_at: ago(9 * DAY - 3 * MIN),
      messages: [
        { id: "c4m1", role: "user", content: "Quick rule of thumb for ser vs estar?", created_at: ago(9 * DAY) },
        {
          id: "c4m2", role: "assistant", model: MODEL, prompt_tokens: 520, completion_tokens: 88, created_at: ago(9 * DAY - MIN),
          content: "- **Ser**: what something *is* — identity, origin, time, traits. *Soy ingeniero.*\n- **Estar**: how or where something *is* — location, states, feelings. *Estoy cansado.*\n\nTrick: \"how you feel and where you are, always use estar.\"",
        },
      ],
    },
  ];

  const proposals: Proposal[] = [
    {
      id: "pr1", conversation_id: "c1", action: "set_reminder",
      payload: { title: "Send proof of insurance to the leasing office", project_id: P.move, project: projectName[P.move], due_text: "in 3 days at 9am", due_date: null, at: localAt(3, 9), rrule: null, repeat: "" },
    },
  ];

  const devices: Device[] = [
    { id: "dev1", name: "Phone", kind: "phone", created_at: ago(20 * DAY), last_seen_at: ago(35 * MIN), revoked_at: null, passkeys: 1 },
    { id: "dev2", name: "Work laptop", kind: "laptop", created_at: ago(12 * DAY), last_seen_at: ago(2 * DAY), revoked_at: null, passkeys: 1 },
  ];

  const settings: SettingField[] = [
    setting("chat_model", "Default model", "model", "model", "Used by new chats unless a chat picks its own.", MODEL),
    setting("extra_models", "Extra allowed models", "model", "models", "Installed Ollama models that chats may pick.", ["llama3.2:3b"]),
    setting("num_ctx", "Context size (tokens)", "model", "int", "How much text the model reads per reply.", 16384, { minimum: 2048, maximum: 131072, restart: true }),
    setting("answer_reserve", "Reply budget (tokens)", "model", "int", "Room kept free for the reply itself.", 1024, { minimum: 256, maximum: 8192 }),
    setting("thinking", "Default thinking level", "model", "choice", "How much models reason before replying, unless a chat picks its own.", "low", { choices: ["off", "low", "medium", "high"] }),
    setting("memory_idle_minutes", "Suggest memories after (minutes idle)", "memory", "int", "pal looks for things worth remembering once a chat goes quiet.", 15, { minimum: 1, maximum: 1440 }),
    setting("session_days", "Sign-in lasts (days)", "security", "int", "How long a sign-in on this PC stays valid.", 30, { minimum: 1, maximum: 365 }),
    setting("device_idle_days", "Paired device idle limit (days)", "security", "int", "A paired device must sign in again after this long unused.", 14, { minimum: 1, maximum: 90 }),
    setting("backup_keep", "Backups to keep", "backup", "int", "Nightly backups older than this many are deleted.", 14, { minimum: 1, maximum: 365 }),
    setting("brief_hour", "Morning brief at (hour, 0 = off)", "automation", "int", "One notification each morning with what's due today.", 8, { minimum: 0, maximum: 23 }),
    setting("rescan_minutes", "Check added folders every (minutes, 0 = off)", "automation", "int", "New or changed files in your folders are read automatically.", 30, { minimum: 0, maximum: 1440 }),
  ];

  return { projects, tasks, notes, reminders, memories, documents, conversations, proposals, devices, settings, defaultThinking: "low" };
}

export const PROJECT_NAMES = projectName;
