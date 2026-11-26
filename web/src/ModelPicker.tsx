import { useEffect, useState } from "react";
import { api, isClaude, ModelList } from "./api";

type Props = {
  value: string | null; // null = the default model
  onPick: (model: string | null) => void;
  onAuthError: (err: unknown) => void;
};

// The chat's model. Picking Claude asks first: from then on this chat's text leaves the PC.
export default function ModelPicker({ value, onPick, onAuthError }: Props) {
  const [models, setModels] = useState<ModelList | null>(null);
  const [pendingClaude, setPendingClaude] = useState<string | null>(null);

  useEffect(() => {
    api.models().then(setModels).catch(onAuthError);
  }, [onAuthError]);

  const pick = (next: string) => {
    if (isClaude(next) && !isClaude(value)) {
      setPendingClaude(next);
      return;
    }
    setPendingClaude(null);
    onPick(next || null);
  };

  return (
    <>
      <label className="picker model-pick" title="Switch with /model name">
        Model{" "}
        <select value={value ?? ""} onChange={(e) => pick(e.target.value)} aria-label="Model for this chat">
          <option value="">Default{models ? ` (${models.default})` : ""}</option>
          <optgroup label="On this PC">
            {models?.models.filter((m) => m.provider !== "claude").map((m) => (
              <option key={m.name} value={m.name} disabled={m.installed === false}>
                {m.name}
                {m.installed === false ? " (not installed)" : ""}
              </option>
            ))}
          </optgroup>
          <optgroup label="Claude (sends this chat to Anthropic)">
            {models?.models.filter((m) => m.provider === "claude").map((m) => (
              <option key={m.name} value={m.name} disabled={!m.installed}>
                {m.name}
                {!m.installed ? " (no API key)" : ""}
              </option>
            ))}
          </optgroup>
        </select>
      </label>
      {models && !models.ollama_up && <span className="error small">Ollama isn't responding.</span>}
      {pendingClaude && (
        <div className="cloud-confirm small" role="alert">
          <p>
            Use <strong>{pendingClaude}</strong> for this chat? Its messages will be sent to Anthropic. Memories,
            documents and notes stay on this PC.
          </p>
          <div className="row-actions">
            <button className="primary small" onClick={() => { setPendingClaude(null); onPick(pendingClaude); }}>
              Use Claude
            </button>
            <button className="small" onClick={() => setPendingClaude(null)}>Cancel</button>
          </div>
        </div>
      )}
    </>
  );
}
