import { FormEvent, useEffect, useMemo, useState } from "react";
import { SteamSection } from "./steam/SteamSection";

type Options = {
  maps: string[];
  weapons: string[];
  opening_kill_sides: string[];
};

type Prediction = {
  predicted_winner: "ct" | "t";
  probabilities: { ct: number; t: number };
};

const apiBase = (import.meta.env.VITE_API_BASE_URL || (import.meta.env.DEV ? "/api" : "https://api.csgooner.com")).replace(/\/$/, "");

function App() {
  const [options, setOptions] = useState<Options | null>(null);
  const [mapName, setMapName] = useState("");
  const [side, setSide] = useState("ct");
  const [seconds, setSeconds] = useState("15.0");
  const [weapon, setWeapon] = useState("");
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${apiBase}/options`)
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not load prediction options.");
        return (await response.json()) as Options;
      })
      .then((data) => {
        setOptions(data);
        setMapName(data.maps[0] ?? "");
        setWeapon(data.weapons[0] ?? "");
      })
      .catch(() => setError("Prediction service is unavailable. Please try again shortly."));
  }, []);

  const winnerLabel = useMemo(() => {
    if (!prediction) return "—";
    return prediction.predicted_winner === "ct" ? "Counter-Terrorists" : "Terrorists";
  }, [prediction]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    setPrediction(null);
    try {
      const response = await fetch(`${apiBase}/predict`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          map_name: mapName,
          opening_kill_side: side,
          opening_kill_seconds: Number(seconds),
          opening_weapon: weapon,
        }),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(typeof body.detail === "string" ? body.detail : "Prediction failed. Check the inputs and try again.");
      }
      setPrediction(body as Prediction);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not reach the prediction service.");
    } finally {
      setLoading(false);
    }
  }

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
              <label className="field">
                <span className="field-label"><span className="field-number">01</span> MAP</span>
                <span className="select-wrap">
                  <select value={mapName} onChange={(event) => setMapName(event.target.value)} disabled={!ready} required>
                    {options?.maps.map((map) => <option value={map} key={map}>{map.replace(/^de_/, "").replaceAll("_", " ")}</option>)}
                  </select><span className="chevron">⌄</span>
                </span>
              </label>

              <fieldset className="field side-field">
                <legend className="field-label"><span className="field-number">02</span> OPENING KILL SIDE</legend>
                <div className="side-options">
                  <button type="button" className={`side-option ct-option ${side === "ct" ? "selected" : ""}`} onClick={() => setSide("ct")} aria-pressed={side === "ct"}>
                    <span className="side-symbol ct-symbol">C</span><span>Counter-Terrorists</span><span className="radio-dot" />
                  </button>
                  <button type="button" className={`side-option t-option ${side === "t" ? "selected" : ""}`} onClick={() => setSide("t")} aria-pressed={side === "t"}>
                    <span className="side-symbol t-symbol">T</span><span>Terrorists</span><span className="radio-dot" />
                  </button>
                </div>
              </fieldset>

              <label className="field">
                <span className="field-label"><span className="field-number">03</span> OPENING KILL TIME <span className="unit">SECONDS INTO ROUND</span></span>
                <span className="number-wrap"><input type="number" min="0" max="120" step="0.1" value={seconds} onChange={(event) => setSeconds(event.target.value)} required /><span className="number-unit">SEC</span></span>
              </label>

              <label className="field">
                <span className="field-label"><span className="field-number">04</span> OPENING WEAPON</span>
                <span className="select-wrap">
                  <select value={weapon} onChange={(event) => setWeapon(event.target.value)} disabled={!ready} required>
                    {options?.weapons.map((item) => <option value={item} key={item}>{item.toUpperCase()}</option>)}
                  </select><span className="chevron">⌄</span>
                </span>
              </label>

              <button className="submit-button" type="submit" disabled={!ready || loading}>
                <span>{loading ? "READING THE ROUND" : "PREDICT ROUND WINNER"}</span><span className="button-arrow">↗</span>
              </button>
              <p className="form-note"><span>✳</span> Based on professional CS2 round data</p>
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
