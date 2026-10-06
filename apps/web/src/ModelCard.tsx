import { useEffect, useState } from "react";
import { apiBase } from "./steam/api";
import { weaponName } from "./steam/weapons";
import type { RoundInputs } from "./roundForm";

type Probs = { ct: number; t: number };

/** How sure the model is, in words (max side probability). */
export function confidenceLabel(p: Probs): { label: string; tone: "strong" | "lean" | "toss" } {
  const top = Math.max(p.ct, p.t);
  if (top >= 0.65) return { label: "Strong lean", tone: "strong" };
  if (top >= 0.55) return { label: "Lean", tone: "lean" };
  return { label: "Toss-up", tone: "toss" };
}

async function predictCt(inputs: RoundInputs): Promise<number | null> {
  try {
    const response = await fetch(`${apiBase}/predict`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ map_name: inputs.mapName, opening_kill_side: inputs.side,
        opening_kill_seconds: Number(inputs.seconds), opening_weapon: inputs.weapon }),
    });
    if (!response.ok) return null;
    const body = await response.json();
    return typeof body?.probabilities?.ct === "number" ? body.probabilities.ct : null;
  } catch {
    return null;
  }
}

type Factor = { id: string; label: string; detail: string; delta: number | null };

const pts = (delta: number) => `${delta > 0 ? "+" : "−"}${Math.abs(Math.round(delta * 100))} pts`;

/** "Why this call?": what moves the estimate, by asking the same model about one change at a
 * time (the other side gets the kill, a later kill, a different weapon). Fetched on open only. */
export function WhyThisRound({ inputs, ct, weapons }: { inputs: RoundInputs; ct: number; weapons: string[] }) {
  const [open, setOpen] = useState(false);
  const [factors, setFactors] = useState<Factor[] | null>(null);
  const key = JSON.stringify(inputs);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setFactors(null);
    const seconds = Number(inputs.seconds);
    const later = seconds + 20 <= 120 ? seconds + 20 : Math.max(0, seconds - 20);
    const altWeapon = inputs.weapon !== "ak47" && weapons.includes("ak47") ? "ak47"
      : weapons.includes("m4a1_silencer") && inputs.weapon !== "m4a1_silencer" ? "m4a1_silencer" : null;
    const otherSide = inputs.side === "ct" ? "t" : "ct";
    const asks: { id: string; label: string; detail: string; inputs: RoundInputs }[] = [
      { id: "side", label: "Who got the opening kill", detail: `If ${otherSide.toUpperCase()} had got it instead`, inputs: { ...inputs, side: otherSide } },
      { id: "time", label: "When it happened", detail: `If it had come at ${later.toFixed(0)}s`, inputs: { ...inputs, seconds: String(later) } },
      ...(altWeapon ? [{ id: "weapon", label: "The weapon", detail: `With ${weaponName(altWeapon)} instead of ${weaponName(inputs.weapon)}`,
        inputs: { ...inputs, weapon: altWeapon } }] : []),
    ];
    Promise.all(asks.map((a) => predictCt(a.inputs))).then((results) => {
      if (cancelled) return;
      // delta: how much CT's chance changes if this one thing were different (negative = this input favours CT)
      setFactors(asks.map((a, i) => ({ id: a.id, label: a.label, detail: a.detail, delta: results[i] == null ? null : results[i]! - ct })));
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, key, ct]);

  return (
    <details className="why-round" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>Why this call?</summary>
      {!factors ? (
        <p className="steam-muted">Asking the model…</p>
      ) : (
        <ul className="why-list" aria-label="What moves the estimate">
          {factors.map((f) => (
            <li key={f.id}>
              <strong>{f.label}</strong>
              <span>{f.delta == null ? "Couldn’t check." : `${f.detail}: CT ${Math.round((ct + f.delta) * 100)}% (${pts(f.delta)} for CT).`}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="steam-muted why-note">Each line changes one input and asks the same model again. The biggest swing is what this estimate rests on most.</p>
    </details>
  );
}

const mapLabel = (map: string) => map.replace(/^de_/, "").replaceAll("_", " ");

/** About the model: what it sees, which maps it covers, how sure it is and where it stops. */
export function ModelCard({ maps }: { maps: string[] }) {
  return (
    <div className="model-card" aria-label="Model card">
      <dl className="model-card-grid">
        <div><dt>INPUTS</dt><dd>Map · side with the opening kill · its time · its weapon</dd></div>
        <div><dt>TRAINED ON</dt><dd>Professional CS2 rounds (OpenCS2 data)</dd></div>
        <div><dt>CONFIDENCE</dt><dd>Toss-up under 55% · Lean 55–65% · Strong lean 65%+</dd></div>
        <div>
          <dt>MAPS COVERED{maps.length ? ` · ${maps.length}` : ""}</dt>
          <dd>{maps.length ? maps.map(mapLabel).join(", ") : "Loading…"}</dd>
        </div>
      </dl>
      <p className="steam-muted model-card-note">
        Rounds on other maps show as unscored in your reports; covering a new map needs pro rounds on it to retrain with.
        It doesn’t see economy, utility, positions or player skill, and it isn’t calibrated for matchmaking.
      </p>
    </div>
  );
}
