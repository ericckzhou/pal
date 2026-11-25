// Theme preference: follows the system by default; Terminal is a dense monospace dark look.
export type Theme = "system" | "light" | "dark" | "terminal";
export const THEMES: { value: Theme; label: string }[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "terminal", label: "Terminal" },
];
const KEY = "pal.theme";

export function loadTheme(): Theme {
  try {
    const t = localStorage.getItem(KEY);
    if (t === "light" || t === "dark" || t === "terminal") return t;
  } catch {
    /* storage blocked */
  }
  return "system";
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* storage blocked */
  }
}
