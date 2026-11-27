// Stroke icons drawn inline (no icon font or remote assets: S5 keeps the page self-contained).
const PATHS: Record<string, string> = {
  status: "M3 12h4l3-8 4 16 3-8h4",
  chat: "M4 5h16v11H9l-5 4z",
  sessions: "M4 6h16M4 12h16M4 18h10",
  work: "M3 7h7l2 2h9v10H3zM3 7V5h6l2 2",
  tasks: "M4 4h4v16H4zM10 4h4v10h-4zM16 4h4v6h-4z",
  memory: "M12 3a6 6 0 0 0-6 6c0 2.5 1.5 4 2.5 5v3h7v-3c1-1 2.5-2.5 2.5-5a6 6 0 0 0-6-6zM9.5 21h5",
  documents: "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6",
  schedule: "M12 7v5l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z",
  analytics: "M5 20V10M12 20V4M19 20v-7",
  digest: "M4 5h16v14H4zM4 9h16M8 13h8M8 16h5",
  flag: "M5 21V4h11l-2 4 2 4H5",
  lock: "M6 11h12v9H6zM9 11V8a3 3 0 0 1 6 0v3",
  logs: "M4 5h16v14H4zM7 9l3 3-3 3M12 15h5",
  settings:
    "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19 12l2-1-1-3-2 .3-1.3-1.3.3-2-3-1-1 2h-2l-1-2-3 1 .3 2L5.7 7.3 4 7 3 10l2 1v2l-2 1 1 3 2-.3 1.3 1.3-.3 2 3 1 1-2h2l1 2 3-1-.3-2 1.3-1.3 2 .3 1-3-2-1z",
  devices: "M7 3h10v18H7zM11 18h2",
  connections: "M9 15l6-6M10 6l1.5-1.5a3.5 3.5 0 0 1 5 5L15 11M14 18l-1.5 1.5a3.5 3.5 0 0 1-5-5L9 13",
  plus: "M12 5v14M5 12h14",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  menu: "M4 6h16M4 12h16M4 18h16",
  rail: "M4 4h16v16H4zM15 4v16",
  play: "M8 5v14l11-7z",
  pause: "M8 5v14M16 5v14",
  trash: "M5 7h14M10 11v6M14 11v6M7 7l1 13h8l1-13M9 7V4h6v3",
  download: "M12 4v11M7 10l5 5 5-5M5 20h14",
  bolt: "M13 3L5 14h6l-1 7 8-11h-6z",
  book: "M5 4h10a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h10",
  check: "M5 12l5 5 9-10",
  terminal: "M4 17l6-5-6-5M12 19h8",
  back: "M15 5l-7 7 7 7",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  note: "M5 4h14v16H5zM8 8h8M8 12h8M8 16h5",
  archive: "M4 5h16v4H4zM5 9v10h14V9M10 13h4",
  edit: "M4 20h4L19 9l-4-4L4 16zM13 7l4 4",
};

export function Icon({ name, size = 16 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg className="icon-svg" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d={PATHS[name] ?? PATHS.chat} />
    </svg>
  );
}
