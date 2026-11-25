import { useEffect, useState } from "react";
import { api, SettingField, SettingsReport } from "./api";
import { Icon } from "./Icon";

type Props = { onAuthError: (err: unknown) => void };

// Built-in jobs pal runs on its own. Their timing lives in the "automation" settings group (PC only).
export default function ScheduleAutomations({ onAuthError }: Props) {
  const [fields, setFields] = useState<SettingField[]>([]);
  const [draft, setDraft] = useState<Record<string, number | "">>({});
  const [busyKey, setBusyKey] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const load = (r: SettingsReport) => {
    const auto = r.fields.filter((f) => f.group === "automation");
    setFields(auto);
    setDraft(Object.fromEntries(auto.map((f) => [f.key, f.value as number])));
  };

  useEffect(() => {
    api.settings().then(load).catch(onAuthError);
  }, [onAuthError]);

  const save = async (f: SettingField) => {
    setBusyKey(f.key);
    setError("");
    setMsg("");
    try {
      load(await api.saveSettings({ [f.key]: draft[f.key] }));
      setMsg("Saved. Applies right away.");
    } catch (err) {
      onAuthError(err);
      setError(err instanceof Error ? err.message : "Could not save.");
    } finally {
      setBusyKey("");
    }
  };

  if (fields.length === 0) return null;

  return (
    <div className="automations">
      <h3>Automations</h3>
      {error && <p className="error small">{error}</p>}
      {msg && <p className="notice small">{msg}</p>}
      {fields.map((f) => {
        const v = draft[f.key];
        const dirty = v !== f.value;
        const off = f.value === 0;
        return (
          <div key={f.key} className="setting">
            <div className="setting-text">
              <label htmlFor={`auto-${f.key}`} className="setting-label">
                {f.label}
                <span className={`state ${off ? "" : "ok"}`}>
                  <span className={`dot ${off ? "" : "ok"}`} aria-hidden="true" />
                  {off ? "Off" : "On"}
                </span>
              </label>
              <p className="muted small">{f.help}</p>
            </div>
            <div className="setting-input">
              <input
                id={`auto-${f.key}`}
                type="number"
                min={f.minimum ?? undefined}
                max={f.maximum ?? undefined}
                value={String(v)}
                onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value === "" ? "" : Number(e.target.value) })}
              />
              <button className="small" onClick={() => save(f)} disabled={!dirty || v === "" || busyKey === f.key}>
                <Icon name="check" size={14} /> Save
              </button>
            </div>
          </div>
        );
      })}
      <div className="setting">
        <div className="setting-text">
          <span className="setting-label">
            Weekly digest
            <span className="state ok"><span className="dot ok" aria-hidden="true" />On</span>
          </span>
          <p className="muted small">Built every Monday from 08:00, with one notification. Open it on the Digest page.</p>
        </div>
      </div>
    </div>
  );
}
