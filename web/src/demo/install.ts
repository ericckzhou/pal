// Demo build only (vite --mode demo): answer /api calls in the browser and show a "demo" badge.
import { handle } from "./router";
import "./demo.css";

function patchFetch() {
  const real = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), location.origin);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) return real(input, init);
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    return handle(method, url, init?.body);
  };
}

function badge() {
  const el = document.createElement("aside");
  el.className = "demo-badge";
  el.innerHTML = `
    <button type="button" class="demo-pill" aria-expanded="false">Demo · made-up data</button>
    <div class="demo-card" hidden>
      <strong>This is a demo of pal.</strong>
      <p>pal is a private assistant that runs entirely on one Windows PC: a local LLM (Ollama), PostgreSQL + pgvector,
      and a FastAPI server, reached from other devices over Tailscale.</p>
      <p>Here the server is replaced by scripted replies and invented data in your browser. Nothing you type is sent anywhere,
      and changes reset when you reload.</p>
      <p class="demo-try">Try the Chat page: <em>What's due this week?</em> · <em>What does my lease say about pets?</em> ·
      <em>Remind me to call Alex tomorrow</em> · <em>/help</em></p>
      <button type="button" class="demo-close">Got it</button>
    </div>`;
  const pill = el.querySelector<HTMLButtonElement>(".demo-pill")!;
  const card = el.querySelector<HTMLDivElement>(".demo-card")!;
  const toggle = (open: boolean) => {
    card.hidden = !open;
    pill.setAttribute("aria-expanded", String(open));
    try {
      localStorage.setItem("pal.demo.seen", "1");
    } catch {
      /* storage blocked */
    }
  };
  pill.addEventListener("click", () => toggle(card.hidden));
  el.querySelector(".demo-close")!.addEventListener("click", () => toggle(false));
  document.body.appendChild(el);
  let seen = false;
  try {
    seen = localStorage.getItem("pal.demo.seen") === "1";
  } catch {
    /* storage blocked */
  }
  if (!seen) card.hidden = false;
}

export function install() {
  patchFetch();
  badge();
  document.title = "pal — demo";
}
