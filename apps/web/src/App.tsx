import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { COMMON_WEAPONS, PRESETS, TIME_CHIPS, type Preset, type RoundInputs, initialInputs, remember, validSeconds } from "./roundForm";
import "./roundForm.css";
import { AccountPage } from "./steam/AccountPage";
import { isAccountPath } from "./steam/routes";
import { SteamSection } from "./steam/SteamSection";
import { weaponName } from "./steam/weapons";

type Options = {
  maps: string[];
  weapons: string[];
  opening_kill_sides: string[];
};

type Prediction = {
  predicted_winner: "ct" | "t";
  probabilities: { ct: number; t: number };
};

const apiBase = (import.meta.env.VITE_API_BASE_URL || (import.meta.env.DEV ? "/api" : "https://api-site.csgooner.com")).replace(/\/$/, "");

const mapLabel = (map: string) => map.replace(/^de_/, "").replaceAll("_", " ");
/** After the first prediction, edits re-predict on their own after this pause. */
const AUTO_PREDICT_MS = 350;

function App() {
  return isAccountPath(window.location.pathname) ? <AccountShell /> : <Home />;
}

/** ``/account``: same brand bar, links back to the home page sections. */
function AccountShell() {
  return (
    <div className="site-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="CS Gooner home">
          <span className="brand-mark"><img src="/favicon.svg" alt="" /></span>
          <span>CS <span className="brand-light">GOONER</span></span>
        </a>
        <nav aria-label="Main navigation">
          <a href="/#predictor">Predictor</a>
          <a href="/#matches">My matches</a>
          <a className="nav-active" href="/account" aria-current="page">Account</a>
        </nav>
      </header>
      <main id="top"><AccountPage /></main>
    </div>
  );
}

function Home() {
  const [options, setOptions] = useState<Options | null>(null);
  const [mapName, setMapName] = useState("");
  const [side, setSide] = useState<"ct" | "t">("ct");
  const [seconds, setSeconds] = useState("15.0");
  const [weapon, setWeapon] = useState("");
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [activePreset, setActivePreset] = useState<string | null>(null);
  // Set once the user has predicted: from then on every edit updates the readout by itself.
  const [live, setLive] = useState(false);

  useEffect(() => {
    fetch(`${apiBase}/options`)
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not load prediction options.");
        return (await response.json()) as Options;
      })
      .then((data) => {
        setOptions(data);
        const start = initialInputs(data.maps, data.weapons);
        setMapName(start.mapName);
        setSide(start.side);
        setSeconds(start.seconds);
        setWeapon(start.weapon);
      })
      .catch(() => setError("Prediction service is unavailable. Please try again shortly."));
  }, []);

  const winnerLabel = useMemo(() => {
    if (!prediction) return "—";
    return prediction.predicted_winner === "ct" ? "Counter-Terrorists" : "Terrorists";
  }, [prediction]);

  const weaponGroups = useMemo(() => {
    const all = options?.weapons ?? [];
    const common = COMMON_WEAPONS.filter((w) => all.includes(w));
    return { common, rest: all.filter((w) => !common.includes(w)) };
  }, [options]);
  const presets = useMemo(() => PRESETS.filter((p) => options?.weapons.includes(p.weapon)), [options]);

  const requestId = useRef(0);
  const lastSent = useRef("");
  const predict = useCallback(async (inputs: RoundInputs) => {
    if (!inputs.mapName || !inputs.weapon || !validSeconds(inputs.seconds)) return;
    lastSent.current = JSON.stringify(inputs);
    const id = ++requestId.current;
    setLoading(true);
    setError("");
    remember(inputs);
    try {
      const response = await fetch(`${apiBase}/predict`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          map_name: inputs.mapName,
          opening_kill_side: inputs.side,
          opening_kill_seconds: Number(inputs.seconds),
          opening_weapon: inputs.weapon,
        }),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(typeof body.detail === "string" ? body.detail : "Prediction failed. Check the inputs and try again.");
      }
      if (id === requestId.current) setPrediction(body as Prediction);
    } catch (reason) {
      if (id === requestId.current) {
        setPrediction(null);
        setError(reason instanceof Error ? reason.message : "Could not reach the prediction service.");
      }
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, []);

  // Live mode: re-predict shortly after any edit (the last request wins).
  useEffect(() => {
    if (!live) return;
    const inputs = { mapName, side, seconds, weapon };
    if (JSON.stringify(inputs) === lastSent.current) return;  // e.g. just submitted
    const timer = setTimeout(() => void predict(inputs), AUTO_PREDICT_MS);
    return () => clearTimeout(timer);
  }, [live, mapName, side, seconds, weapon, predict]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLive(true);
    void predict({ mapName, side, seconds, weapon });
  }

  function applyPreset(preset: Preset) {
    setActivePreset(preset.id);
    setSide(preset.side);
    setSeconds(String(preset.seconds));
    setWeapon(preset.weapon);
    setLive(true);  // the live effect predicts with the preset's values
  }

  // Any manual edit means the inputs no longer match a preset.
  const edit = <T,>(set: (value: T) => void) => (value: T) => { setActivePreset(null); set(value); };

  const ready = Boolean(options?.maps.length && options?.weapons.length);
  const ctPercent = prediction ? prediction.probabilities.ct * 100 : 50;
  const tPercent = prediction ? prediction.probabilities.t * 100 : 50;

  return (
    <div className="site-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="CS Gooner home">
          <span className="brand-mark"><img src="/favicon.svg" alt="" /></span>
          <span>CS <span className="brand-light">GOONER</span></span>
        </a>
        <nav aria-label="Main navigation">
          <a className="nav-active" href="#predictor">Predictor</a>
          <a href="#matches">My matches</a>
          <a href="#about">About the model</a>
          <a href="https://public.tableau.com/app/profile/nicholas.hinkel/viz/CS2-Analytics/CS2RoundAnalytics?publish=yes" target="_blank" rel="noreferrer">Analytics ↗</a>
        </nav>
        <div className="live-indicator"><span /> MODEL ONLINE</div>
      </header>

      <main id="top">
        <section className="hero">
          <div className="hero-copy">
            <div className="eyebrow"><span className="eyebrow-line" /> ROUND INTELLIGENCE <span className="eyebrow-index">01 / 03</span></div>
            <h1>Every round<br />has a <em>turning point.</em></h1>
            <p>Set the scene. See how the opening duel shifts the odds.</p>
          </div>
          <div className="hero-stamp" aria-hidden="true">
            <span>ROUND</span><strong>01</strong><i>LIVE MODEL</i>
          </div>
          <div className="hero-grid" aria-hidden="true" />
        </section>

        <section id="predictor" className="workspace" aria-label="Round winner predictor">
          <div className="form-panel">
            <div className="panel-heading">
              <div><span className="section-kicker">MATCH SETUP</span><h2>Build the round</h2></div>
              <span className="step-count">01 <i>—</i> 04</span>
            </div>
            <form onSubmit={submit}>
              {presets.length > 0 && (
                <div className="field preset-field" role="group" aria-label="Quick presets">
                  <span className="field-label">QUICK START <span className="unit">ONE TAP · KEEPS YOUR MAP</span></span>
                  <div className="preset-row">
                    {presets.map((preset) => (
                      <button type="button" key={preset.id} className={`chip preset-chip ${activePreset === preset.id ? "selected" : ""}`}
                        aria-pressed={activePreset === preset.id} onClick={() => applyPreset(preset)} disabled={!ready}>
                        <strong>{preset.label}</strong><span>{preset.detail}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <label className="field">
                <span className="field-label"><span className="field-number">01</span> MAP</span>
                <span className="select-wrap">
                  <select value={mapName} onChange={(event) => setMapName(event.target.value)} disabled={!ready} required aria-label="Map">
                    {options?.maps.map((map) => <option value={map} key={map}>{mapLabel(map)}</option>)}
                  </select><span className="chevron">⌄</span>
                </span>
              </label>

              <fieldset className="field side-field">
                <legend className="field-label"><span className="field-number">02</span> OPENING KILL SIDE</legend>
                <div className="side-options">
                  <button type="button" className={`side-option ct-option ${side === "ct" ? "selected" : ""}`} onClick={() => edit(setSide)("ct")} aria-pressed={side === "ct"}>
                    <span className="side-symbol ct-symbol">C</span><span>Counter-Terrorists</span><span className="radio-dot" />
                  </button>
                  <button type="button" className={`side-option t-option ${side === "t" ? "selected" : ""}`} onClick={() => edit(setSide)("t")} aria-pressed={side === "t"}>
                    <span className="side-symbol t-symbol">T</span><span>Terrorists</span><span className="radio-dot" />
                  </button>
                </div>
              </fieldset>

              <label className="field">
                <span className="field-label"><span className="field-number">03</span> OPENING KILL TIME <span className="unit">SECONDS INTO ROUND</span></span>
                <span className="number-wrap"><input type="number" inputMode="decimal" min="0" max="120" step="0.1" value={seconds} onChange={(event) => edit(setSeconds)(event.target.value)} required aria-label="Opening kill time in seconds" /><span className="number-unit">SEC</span></span>
              </label>
              <div className="time-chips" role="group" aria-label="Common opening kill times">
                {TIME_CHIPS.map((value) => (
                  <button type="button" key={value} className={`chip ${Number(seconds) === value ? "selected" : ""}`}
                    aria-pressed={Number(seconds) === value} onClick={() => edit(setSeconds)(String(value))}>{value}s</button>
                ))}
              </div>

              <label className="field">
                <span className="field-label"><span className="field-number">04</span> OPENING WEAPON</span>
                <span className="select-wrap">
                  <select value={weapon} onChange={(event) => edit(setWeapon)(event.target.value)} disabled={!ready} required aria-label="Opening weapon">
                    {weaponGroups.common.length > 0 && (
                      <optgroup label="Common">
                        {weaponGroups.common.map((item) => <option value={item} key={item}>{weaponName(item)}</option>)}
                      </optgroup>
                    )}
                    <optgroup label={weaponGroups.common.length ? "All weapons" : "Weapons"}>
                      {weaponGroups.rest.map((item) => <option value={item} key={item}>{weaponName(item)}</option>)}
                    </optgroup>
                  </select><span className="chevron">⌄</span>
                </span>
              </label>

              <button className="submit-button" type="submit" disabled={!ready || loading}>
                <span>{loading ? "READING THE ROUND" : live ? "UPDATE PREDICTION" : "PREDICT ROUND WINNER"}</span><span className="button-arrow">↗</span>
              </button>
              <p className="form-note"><span>✳</span> {live ? "Live: the readout updates as you change the round" : "Based on professional CS2 round data"}</p>
            </form>
          </div>

          <div className="result-panel" aria-live="polite">
            <div className="result-topline"><span className="section-kicker">MODEL READOUT</span><span className={`result-status ${prediction ? "has-result" : ""}`}><i /> {prediction ? "PREDICTION READY" : "AWAITING INPUT"}</span></div>
            <div className="result-main">
              <span className="result-label">PREDICTED ROUND WINNER</span>
              <div className={`winner-name ${prediction?.predicted_winner ?? ""}`}>{winnerLabel}<span className="winner-arrow">↗</span></div>
              <div className="probability-summary"><span>WIN PROBABILITY</span><strong>{prediction ? `${Math.round(Math.max(ctPercent, tPercent))}%` : "—"}</strong></div>
            </div>
            <div className="probability-card">
              <div className="probability-heading"><span>WIN PROBABILITY BY SIDE</span><span>MODEL ESTIMATE</span></div>
              <div className="prob-row">
                <div className="prob-label"><span className="side-symbol ct-symbol">C</span><span>CT</span><strong>{prediction ? `${ctPercent.toFixed(1)}%` : "—"}</strong></div>
                <div className="prob-track"><span className="prob-fill ct-fill" style={{ width: `${ctPercent}%` }} /></div>
              </div>
              <div className="prob-row">
                <div className="prob-label"><span className="side-symbol t-symbol">T</span><span>T</span><strong>{prediction ? `${tPercent.toFixed(1)}%` : "—"}</strong></div>
                <div className="prob-track"><span className="prob-fill t-fill" style={{ width: `${tPercent}%` }} /></div>
              </div>
            </div>
            <div className="result-foot"><span>CS2 ROUND WINNER MODEL</span><span>LOGISTIC REGRESSION <i>●</i></span></div>
            {error && <div className="error-message" role="alert">{error}</div>}
          </div>
        </section>

        <SteamSection />

        <section id="about" className="model-info">
          <div className="info-intro"><span className="section-kicker">THE MODEL</span><h2>One opening duel.<br /><em>A lot of signal.</em></h2></div>
          <p>Trained on professional Counter-Strike 2 rounds, the model estimates which side wins using the map, opening-kill side and timing, and weapon. It is a historical estimate, not a guarantee of the outcome.</p>
          <div className="accuracy"><span>HELD-OUT ACCURACY</span><strong>71.8<small>%</small></strong><span className="accuracy-caption">Logistic regression · project evaluation</span></div>
        </section>
      </main>

      <footer><a className="brand footer-brand" href="#top" aria-label="CS Gooner home"><span className="brand-mark"><img src="/favicon.svg" alt="" /></span><span>CS <span className="brand-light">GOONER</span></span></a><span>BUILT AROUND THE ROUND.</span><span>CS2 ANALYTICS PROJECT <i>© 2026</i></span></footer>
    </div>
  );
}

export default App;
