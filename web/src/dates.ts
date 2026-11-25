// Local-time date helpers for tasks and reminders (tasks carry a date only; reminders carry the time).

export const REPEATS: [string, string][] = [
  ["", "Once"],
  ["FREQ=DAILY", "Every day"],
  ["FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "Every weekday"],
  ["FREQ=WEEKLY", "Every week"],
  ["FREQ=WEEKLY;INTERVAL=2", "Every 2 weeks"],
  ["FREQ=MONTHLY", "Every month"],
  ["FREQ=YEARLY", "Every year"],
];

const pad = (n: number) => String(n).padStart(2, "0");

export function localDate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
export const today = () => localDate(new Date());

export function localDateTime(d: Date): string {
  return `${localDate(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// A due date ("YYYY-MM-DD"): today / tomorrow by name, else a short weekday date.
export function formatDue(d: string, now = today()): string {
  if (d === now) return "Today";
  if (d === localDate(new Date(new Date(`${now}T00:00`).getTime() + 86_400_000))) return "Tomorrow";
  return new Date(`${d}T00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}
