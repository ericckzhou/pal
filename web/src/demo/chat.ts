// Scripted replies for the demo, streamed in the same NDJSON event format as the real server.
import type { Memory, Message, Proposal, Source, Step, StreamEvent } from "../api";
import { dateIn, localAt, DemoConversation, DemoState, MODEL, newId } from "./data";
import { openTasks } from "./views";

type Script = { steps?: Step[]; sources?: Source[]; text: string; proposal?: Proposal; memory?: Memory; model?: string };

const docSource = (s: DemoState, id: string, snippet: string, page: number | null): Source => ({
  handle: id, kind: "document", id, title: s.documents.find((d) => d.id === id)?.title ?? id, snippet, page,
  conversation_id: null, document_id: id, at: null, pinned: false,
});

const HELP = [
  "Commands:",
  "- `/task <title>` — draft a task",
  "- `/remind <what> tomorrow` — draft a reminder",
  "- `/model` — list models",
  "",
  "Or just ask: *what's due this week?*, *what does my lease say about pets?*, *remember that I prefer mornings*.",
].join("\n");

const ABOUT =
  "I'm **pal**, a private assistant that runs entirely on one Windows PC:\n\n" +
  "- **Model:** a local LLM through Ollama (here, `qwen3.5:4b`). No cloud by default.\n" +
  "- **Memory & search:** PostgreSQL + pgvector, keyword and vector results fused by rank.\n" +
  "- **Documents:** PDFs read locally with Docling (layout, tables, OCR) and cited by page.\n" +
  "- **Access:** phones and laptops reach it over Tailscale, signing in with per-device passkeys.\n\n" +
  "This page is a **demo**: the server is replaced by scripted replies and made-up data, so nothing you type leaves your browser.";

function draft(c: DemoConversation, action: Proposal["action"], title: string, when: string | null, s: DemoState): Proposal {
  const project = s.projects.find((p) => p.id === c.project_id) ?? null;
  const tomorrow = /tomorrow/i.test(when ?? "");
  // "at 5pm", "at 7:30 am", "at 17:00": the demo reads simple times; pal itself parses dates in code (when.py).
  const clock = (when ?? "").match(/\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?/i);
  let hour = tomorrow ? 9 : 18;
  const minute = clock?.[2] ? Number(clock[2]) : 0;
  if (clock) {
    const h = Number(clock[1]);
    const half = clock[3]?.toLowerCase();
    hour = half ? (h % 12) + (half === "pm" ? 12 : 0) : h < 8 ? h + 12 : h; // a bare "at 5" means 5 pm
  }
  return {
    id: newId(), conversation_id: c.id, action,
    payload: {
      title, project_id: project?.id ?? null, project: project?.name ?? null, due_text: when,
      due_date: action === "create_task" ? (when ? dateIn(tomorrow ? 1 : 3) : null) : null,
      at: action === "set_reminder" ? localAt(tomorrow ? 1 : 0, hour, minute) : null, rrule: null, repeat: "",
    },
  };
}

function script(s: DemoState, c: DemoConversation, input: string): Script {
  const text = input.trim();
  const lower = text.toLowerCase();
  const cmd = (label: string, detail: string | null = null): Step[] => [{ kind: "command", label, detail }];

  if (lower === "/help") return { steps: cmd("/help"), text: HELP, model: "command" };
  if (lower.startsWith("/model"))
    return { steps: cmd("/model"), text: `Models:\n- **${MODEL}** (default, local)\n- llama3.2:3b (local)\n- claude-sonnet-5-5 — needs an API key`, model: "command" };
  const task = text.match(/^\/task\s+(.+)/i);
  if (task) {
    const p = draft(c, "create_task", task[1], null, s);
    return { steps: cmd("/task", task[1]), text: "Drafted a task. Confirm it below.", proposal: p, model: "command" };
  }
  const remindCmd = text.match(/^\/remind\s+(.+?)(\s+(tomorrow|today|tonight).*)?$/i);
  if (remindCmd) {
    const p = draft(c, "set_reminder", remindCmd[1], remindCmd[2]?.trim() ?? "today", s);
    return { steps: cmd("/remind", remindCmd[1]), text: "Drafted a reminder. Confirm it below.", proposal: p, model: "command" };
  }
  if (text.startsWith("/")) return { steps: cmd(text.split(/\s/)[0]), text: "Unknown command. Type /help.", model: "command" };

  const remember = text.match(/^remember(?: that)?\s+(.+)/i);
  if (remember) {
    const fact = remember[1].replace(/\.$/, "");
    const memory: Memory = {
      id: newId(), text: fact.charAt(0).toUpperCase() + fact.slice(1) + ".", kind: "fact", origin: "stated",
      status: "confirmed", pinned: false, source_conversation_id: c.id, updated_at: new Date().toISOString(), project_id: null,
    };
    return { steps: [{ kind: "memory", label: "Saved to memory", detail: memory.text }], memory, text: "Got it — I'll remember that." };
  }

  const remindMe = text.match(/remind me to (.+?)((?:\s+(?:tomorrow|today|tonight|on \w+))?(?:\s+at\s+.+?)?)[.?!]?$/i);
  if (remindMe) {
    const p = draft(c, "set_reminder", remindMe[1], remindMe[2]?.trim() || "today", s);
    return { steps: [{ kind: "proposal", label: "Drafted a reminder", detail: p.payload.title }], proposal: p, text: "I drafted that reminder — check the time and confirm it below." };
  }
  const todo = text.match(/(?:add a task(?: to)?|i need to|todo:?)\s+(.+?)[.?!]?$/i);
  if (todo) {
    const p = draft(c, "create_task", todo[1], null, s);
    return { steps: [{ kind: "proposal", label: "Drafted a task", detail: p.payload.title }], proposal: p, text: "Drafted a task for that. Pick a due date if you like, then confirm." };
  }

  if (/lease|insurance|deposit|pet|landlord|rent\b/.test(lower)) {
    const pets = /pet|dog|cat/.test(lower);
    const src = pets
      ? docSource(s, "d1", "11. PETS. One cat or dog under 25 lb is permitted with a one-time pet deposit of $400. Additional pets require written consent.", 7)
      : docSource(s, "d1", "4. SECURITY DEPOSIT. $2,100, returned within 21 days of move-out less itemized deductions. 14. INSURANCE. Liability coverage of not less than $100,000.", 2);
    return {
      steps: [{ kind: "retrieval", label: "Searched your documents", detail: "1 passage from 1 document" }],
      sources: [src],
      text: pets
        ? "Your lease allows **one cat or dog under 25 lb**, with a one-time **$400 pet deposit** [d1]. A second pet needs the landlord's written consent [d1]."
        : "From your lease [d1]:\n\n- **Security deposit:** $2,100, returned within 21 days of move-out.\n- **Insurance:** at least $100,000 liability coverage, landlord named as interested party.",
    };
  }
  if (/run|marathon|training|pace|km\b/.test(lower)) {
    return {
      steps: [{ kind: "retrieval", label: "Searched your documents", detail: "1 passage from 1 document" }],
      sources: [docSource(s, "d3", "WEEK 7 — Tue: 7 km tempo · Thu: 5 km easy · Sat: 16 km long run · Sun: rest. Taper begins week 11.", 5)],
      text: "Next week is week 7 [d3]: a **7 km tempo** Tuesday, an easy 5 km Thursday and a **16 km long run** Saturday. Your taper starts in week 11 [d3].\n\nYou noted a tight left knee after hills, so keep Thursday's mobility session.",
    };
  }
  if (/due|today|this week|my tasks|to ?do|overdue|what should i/.test(lower)) {
    const open = openTasks(s).filter((t) => t.due_date).sort((a, b) => (a.due_date ?? "").localeCompare(b.due_date ?? ""));
    const lines = open.slice(0, 6).map((t) => `- ${t.due_date! < dateIn(0) ? "⚠️ " : ""}**${t.title}** — ${t.due_date}${t.project ? ` · ${t.project}` : ""}`);
    return {
      steps: [{ kind: "retrieval", label: "Read your tasks", detail: `${open.length} open with due dates` }],
      text: `Here's what's coming up:\n\n${lines.join("\n")}\n\nThe overdue insurance quotes block getting your keys, so I'd start there.`,
    };
  }
  if (/who are you|what are you|how do you work|privacy|private|local|demo|architecture|built/.test(lower)) return { text: ABOUT };
  if (/^(hi|hey|hello|yo)\b/.test(lower)) return { text: "Hi! Ask me what's due this week, about your lease or training plan, or type **/help**." };

  return {
    text:
      "In the real pal, a local model would answer this using your memories, documents and tasks.\n\n" +
      "This demo only has scripted replies. Try:\n- *What's due this week?*\n- *What does my lease say about pets?*\n- *Remind me to call Alex tomorrow*\n- *Remember that I prefer morning meetings*\n- *How do you work?*",
  };
}

const THOUGHTS = [
  "The user is asking about their own data, so check retrieved context first. ",
  "Cite only handles that were provided. Keep it short — they prefer bullet points.",
];

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function chunks(text: string): string[] {
  return text.match(/\S+\s*|\s+/g) ?? [text];
}

/** Runs the script, saves both messages into the conversation, and streams the events. */
export function replyStream(s: DemoState, c: DemoConversation, content: string): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  const plan = script(s, c, content);
  const level = c.thinking ?? s.defaultThinking;
  const think = plan.model !== "command" && level !== "off";

  return new ReadableStream({
    async start(ctrl) {
      const emit = (ev: StreamEvent) => ctrl.enqueue(enc.encode(JSON.stringify(ev) + "\n"));
      const user: Message = { id: newId(), role: "user", content, created_at: new Date().toISOString() };
      c.messages.push(user);
      if (c.title === "New chat") c.title = content.replace(/^\//, "").slice(0, 48);

      for (const step of plan.steps ?? []) {
        await sleep(350);
        emit({ type: "step", step });
      }
      if (plan.memory) {
        s.memories.unshift(plan.memory);
        emit({ type: "memory_saved", memory: plan.memory });
      }
      if (plan.sources) emit({ type: "sources", sources: plan.sources });
      if (think) {
        for (const t of THOUGHTS) for (const piece of chunks(t)) {
          await sleep(22);
          emit({ type: "thinking", text: piece });
        }
        await sleep(250);
      } else await sleep(300);
      for (const piece of chunks(plan.text)) {
        await sleep(18 + Math.random() * 22);
        emit({ type: "token", text: piece });
      }
      if (plan.proposal) {
        s.proposals.push(plan.proposal);
        emit({ type: "proposal", proposal: plan.proposal });
      }
      const model = plan.model ?? c.model ?? MODEL;
      const usage = model === "command" ? undefined : { prompt_tokens: 900 + Math.round(Math.random() * 2400), completion_tokens: Math.round(plan.text.length / 4) };
      const reply: Message = {
        id: newId(), role: "assistant", content: plan.text, model, steps: plan.steps ?? null, sources: plan.sources ?? null,
        created_at: new Date().toISOString(), ...(usage ?? {}),
      };
      c.messages.push(reply);
      c.updated_at = new Date().toISOString();
      emit({ type: "done", message_id: reply.id!, model, usage });
      ctrl.close();
    },
  });
}
