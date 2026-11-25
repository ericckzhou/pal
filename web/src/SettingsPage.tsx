import { useEffect, useState } from "react";
import { api, SettingField, SettingsReport } from "./api";
import { Icon } from "./Icon";

type Props = { onAuthError: (err: unknown) => void; onSaved: () => void };
type Value = SettingField["value"];

const GROUPS: { key: string; label: string }[] = [
  { key: "model", label: "Model" },
  { key: "memory", label: "Memory" },
  { key: "security", label: "Sign-in" },
  { key: "backup", label: "Backups" },
];
const same = (a: Value, b: Value) => JSON.stringify(a) === JSON.stringify(b);

// A form over pal's changeable settings, built from what the server says each field is.
export default function SettingsPage({ onAuthError, onSaved }: Props) {
  const [report, setReport] = useState<SettingsReport | null>(null);
  const [draft, setDraft] = useState<Record<string, Value>>({});
  const [group, setGroup] = useState("model");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = (r: SettingsReport) => {
    setReport(r);
    setDraft(Object.fromEntries(r.fields.map((f) => [f.key, f.value])));
  };

  useEffect(() => {
    api.settings().then(load).catch(onAuthError);
  }, [onAuthError]);

  if (!report) return <section className="page"><p className="muted">Loading…</p></section>;

  const changes = Object.fromEntries(
    report.fields.filter((f) => !same(draft[f.key], f.value)).map((f) => [f.key, draft[f.key]]),
  );
  const dirty = Object.keys(changes).length > 0;
  const needsRestart = report.fields.some((f) => f.restart && f.key in changes);
  const extra = (draft.extra_models as string[]) ?? [];
  // Built-in allowlist (allowed minus the saved extras) plus whatever extras are ticked in the draft.
  const savedExtra = (report.fields.find((f) => f.key === "extra_models")?.value as string[]) ?? [];
  const builtIn = report.allowed_models.filter((m) => !savedExtra.includes(m));
  const modelChoices = [...new Set([...builtIn, ...extra])].sort();

  const save = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      load(await api.saveSettings(changes));
      setNotice(needsRestart ? "Saved. Some changes apply after pal restarts." : "Saved. Changes are live.");
      onSaved();
    } catch (err) {
      onAuthError(err);
      setError(err instanceof Error ? err.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  };

  const reset = async (key: string) => {
    try {
      load(await api.resetSetting(key));
      setNotice("Back to the built-in value.");
      onSaved();
    } catch (err) {
      onAuthError(err);
      setError(err instanceof Error ? err.message : "Could not reset.");
    }
  };

  const field = (f: SettingField) => {
    const v = draft[f.key];
    const id = `set-${f.key}`;
    let input;
    if (f.kind === "int") {
      input = (
        <input
          id={id}
          type="number"
          min={f.minimum ?? undefined}
          max={f.maximum ?? undefined}
          value={String(v)}
          onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value === "" ? "" : Number(e.target.value) })}
        />
      );
    } else if (f.kind === "model") {
      input = (
        <select id={id} value={String(v)} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}>
          {modelChoices.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      );
    } else {
      const installed = report.installed_models;
      input = installed === null ? (
        <p className="error small">Ollama isn't responding, so installed models can't be listed.</p>
      ) : (
        <div className="checks" id={id}>
          {installed.filter((m) => !builtIn.includes(m)).length === 0 && (
            <p className="muted small">Every installed model is already allowed. Install another with “ollama pull”.</p>
          )}
          {installed
            .filter((m) => !builtIn.includes(m))
            .map((m) => (
              <label key={m} className="check">
                <input
                  type="checkbox"
                  checked={extra.includes(m)}
                  onChange={(e) =>
                    setDraft({ ...draft, [f.key]: e.target.checked ? [...extra, m].sort() : extra.filter((x) => x !== m) })
                  }
                />
                <span className="mono">{m}</span>
              </label>
            ))}
        </div>
      );
    }
    return (
      <div key={f.key} className="setting">
        <div className="setting-text">
          <label htmlFor={id} className="setting-label">
            {f.label}
            {f.restart && <span className="badge">restart</span>}
            {f.changed && <span className="badge stated">changed</span>}
          </label>
          <p className="muted small">
            {f.help}
            {f.kind === "int" && ` (${f.minimum}–${f.maximum}, built-in ${f.default})`}
          </p>
        </div>
        <div className="setting-input">
          {input}
          {f.changed && (
            <button className="link small" onClick={() => reset(f.key)}>Reset</button>
          )}
        </div>
      </div>
    );
  };

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h2>Settings</h2>
          <p className="muted small">Only on this PC. Addresses, ports and passwords stay on the command line.</p>
        </div>
        <button className="primary" onClick={save} disabled={!dirty || busy}>
          <Icon name="check" size={14} /> {busy ? "Saving…" : "Save changes"}
        </button>
      </header>
      {error && <p className="error">{error}</p>}
      {notice && <p className="notice small">{notice}</p>}
      <div className="settings-layout">
        <div className="tabs vertical" role="tablist" aria-label="Setting groups">
          {GROUPS.map((g) => (
            <button key={g.key} role="tab" aria-selected={group === g.key} onClick={() => setGroup(g.key)}>
              {g.label}
            </button>
          ))}
        </div>
        <div className="settings-fields">{report.fields.filter((f) => f.group === group).map(field)}</div>
      </div>
    </section>
  );
}
