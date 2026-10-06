import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { COMMON_WEAPONS, PRESETS, TIME_CHIPS, type Preset, type RoundInputs, initialInputs, remember, validSeconds } from "./roundForm";
import "./roundForm.css";
import { AccountPage } from "./steam/AccountPage";
import { ExportPage } from "./steam/ExportPage";
import { StatsPage } from "./steam/StatsPage";
import {
  ABOUT_PATH, ACCOUNT_PATH, PREDICTOR_HASH, STATS_PATH,
  isPredictPath, type AppPage, pageFromPath,
} from "./steam/routes";
import { ModelCard, WhyThisRound, confidenceLabel } from "./ModelCard";
import { GuestHero } from "./steam/GuestHero";
import { SteamSection } from "./steam/SteamSection";
import { useMe, useSteamStatus } from "./steam/hooks";
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

const mapLabel = (map: string) => map.replace(/^de_/i, "").replaceAll("_", " ").toLowerCase();
/** After the first prediction, edits re-predict on their own after this pause. */
const AUTO_PREDICT_MS = 350;

function App() {
  // Legacy /predict → home with #predictor so old bookmarks still land on the form.
  useEffect(() => {
    if (!isPredictPath(window.location.pathname)) return;
    window.history.replaceState(null, "", PREDICTOR_HASH);
  }, []);
  const page = pageFromPath(window.location.pathname);
  return (
    <div className="site-shell">
      <TopBar page={page} />
      <main id="top">
        {page === "account" && <AccountPage />}
        {page === "export" && <ExportPage />}
        {page === "stats" && <StatsPage />}
        {page === "about" && <AboutPage />}
        {page === "home" && <HomeMain />}
      </main>
      <SiteFooter />
    </div>
  );
}

/** Home: guest hero · predictor (primary) · Sync/Upload · 4 tiles · match list. */
function HomeMain() {
  return (
    <>
      <GuestHero />
      <PredictorWorkspace />
      <SteamSection />
    </>
  );
}

function SiteFooter() {
  return (
    <footer>
      <a className="brand footer-brand" href="/" aria-label="CS Gooner home">
        <span className="brand-mark"><img src="/favicon.svg" alt="" /></span>
        <span>CS <span className="brand-light">GOONER</span></span>
      </a>
      <span>CS2 ANALYTICS PROJECT <i>© 2026</i></span>
    </footer>
  );
}

function AboutPage() {
  const [maps, setMaps] = useState<string[]>([]);
  useEffect(() => {
    fetch(`${apiBase}/options`)
      .then(async (response) => (response.ok ? (await response.json()) as Options : null))
      .then((data) => { if (data?.maps) setMaps(data.maps); })
      .catch(() => undefined);
  }, []);
  return (
    <section id="about" className="model-info page-about" aria-label="About the model">
      <div className="info-intro">
        <a className="account-back" href="/">← Home</a>
        <span className="section-kicker">THE MODEL</span>
        <h2>One opening duel.<br /><em>A lot of signal.</em></h2>
      </div>
      <p>Trained on pro CS2 rounds. Historical estimate — not a guarantee.</p>
      <div className="accuracy"><span>HELD-OUT ACCURACY</span><strong>71.8<small>%</small></strong></div>
      <ModelCard maps={maps} />
    </section>
  );
}

function PredictorWorkspace() {
  const [options, setOptions] = useState<Options | null>(null);
  const [mapName, setMapName] = useState("");
  const [side, setSide] = useState<"ct" | "t">("ct");
  const [seconds, setSeconds] = useState("15.0");
  const [weapon, setWeapon] = useState("");
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [activePreset, setActivePreset] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [lastInputs, setLastInputs] = useState<RoundInputs>({ mapName: "", side: "ct", seconds: "15.0", weapon: "" });

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
      if (id === requestId.current) { setPrediction(body as Prediction); setLastInputs(inputs); }
    } catch (reason) {
      if (id === requestId.current) {
        setPrediction(null);
        setError(reason instanceof Error ? reason.message : "Could not reach the prediction service.");
      }
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!live) return;
    const inputs = { mapName, side, seconds, weapon };
    if (JSON.stringify(inputs) === lastSent.current) return;
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
    setLive(true);
  }

  const edit = <T,>(set: (value: T) => void) => (value: T) => { setActivePreset(null); set(value); };

  const ready = Boolean(options?.maps.length && options?.weapons.length);
  const ctPercent = prediction ? prediction.probabilities.ct * 100 : 50;
  const tPercent = prediction ? prediction.probabilities.t * 100 : 50;

  return (
    <section id="predictor" className="workspace" aria-label="Round winner predictor">
      <div className="form-panel">
        <div className="panel-heading">
          <div><h2>Round</h2></div>
        </div>
        <form onSubmit={submit}>
          {presets.length > 0 && (
            <div className="field preset-field" role="group" aria-label="Quick presets">
              <span className="field-label">PRESETS</span>
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
            <span className="field-label">Map</span>
            <span className="select-wrap">
              <select value={mapName} onChange={(event) => setMapName(event.target.value)} disabled={!ready} required aria-label="Map">
                {options?.maps.map((map) => <option value={map} key={map}>{mapLabel(map)}</option>)}
              </select><span className="chevron">⌄</span>
            </span>
          </label>

          <fieldset className="field side-field">
            <legend className="field-label">Side</legend>
            <div className="side-options">
              <button type="button" className={`side-option ct-option ${side === "ct" ? "selected" : ""}`} onClick={() => edit(setSide)("ct")} aria-pressed={side === "ct"}>
                <span className="side-symbol ct-symbol">C</span><span>CT</span><span className="radio-dot" />
              </button>
              <button type="button" className={`side-option t-option ${side === "t" ? "selected" : ""}`} onClick={() => edit(setSide)("t")} aria-pressed={side === "t"}>
                <span className="side-symbol t-symbol">T</span><span>T</span><span className="radio-dot" />
              </button>
            </div>
          </fieldset>

          <label className="field">
            <span className="field-label">Time</span>
            <span className="number-wrap"><input type="number" inputMode="decimal" min="0" max="120" step="0.1" value={seconds} onChange={(event) => edit(setSeconds)(event.target.value)} required aria-label="Opening kill time in seconds" /><span className="number-unit">SEC</span></span>
          </label>
          <div className="time-chips" role="group" aria-label="Common opening kill times">
            {TIME_CHIPS.map((value) => (
              <button type="button" key={value} className={`chip ${Number(seconds) === value ? "selected" : ""}`}
                aria-pressed={Number(seconds) === value} onClick={() => edit(setSeconds)(String(value))}>{value}s</button>
            ))}
          </div>

          <label className="field">
            <span className="field-label">Weapon</span>
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
            <span>{loading ? "…" : "Predict"}</span><span className="button-arrow">↗</span>
          </button>
          <p className="form-note"><span>✳</span> Pro data</p>
        </form>
      </div>

      <div className="result-panel" aria-live="polite">
        <div className="result-topline">
          <span className="section-kicker">READOUT</span>
          <span className={`result-status ${prediction ? "has-result" : ""}`} aria-hidden="true"><i /></span>
        </div>
        {prediction ? (
          <>
            <div className="result-main">
              <span className="result-label">Winner</span>
              <div className={`winner-name ${prediction.predicted_winner}`}>{winnerLabel}<span className="winner-arrow">↗</span></div>
              <div className="probability-summary">
                <strong>{`${Math.round(Math.max(ctPercent, tPercent))}%`}</strong>
                <span className={`confidence-chip ${confidenceLabel(prediction.probabilities).tone}`}>{confidenceLabel(prediction.probabilities).label}</span>
              </div>
            </div>
            <div className="probability-card">
              <div className="probability-heading"><span>By side</span></div>
              <div className="prob-row">
                <div className="prob-label"><span className="side-symbol ct-symbol">C</span><span>CT</span><strong>{`${ctPercent.toFixed(1)}%`}</strong></div>
                <div className="prob-track"><span className="prob-fill ct-fill" style={{ width: `${ctPercent}%` }} /></div>
              </div>
              <div className="prob-row">
                <div className="prob-label"><span className="side-symbol t-symbol">T</span><span>T</span><strong>{`${tPercent.toFixed(1)}%`}</strong></div>
                <div className="prob-track"><span className="prob-fill t-fill" style={{ width: `${tPercent}%` }} /></div>
              </div>
            </div>
          </>
        ) : (
          <div className="readout-empty" role="status">
            <span className="readout-empty-mark" aria-hidden="true" />
            <strong>{loading ? "…" : "Predict"}</strong>
          </div>
        )}
        {prediction && <WhyThisRound inputs={lastInputs} ct={prediction.probabilities.ct} weapons={options?.weapons ?? []} />}
        <div className="result-foot"><span>Logistic regression</span></div>
        {error && <div className="error-message" role="alert">{error}</div>}
      </div>
    </section>
  );
}

/** Brand, main navigation. Below 640px the links fold into a menu. */
function TopBar({ page }: { page: AppPage }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const { enabled } = useSteamStatus();
  const { me } = useMe(enabled);
  const steamUser = me && me.account !== "guest" ? me : null;

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setMenuOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const active = (id: AppPage) => (page === id ? "nav-active" : undefined);
  const current = (id: AppPage) => (page === id ? "page" as const : undefined);

  return (
    <header className={`topbar ${menuOpen ? "menu-open" : ""}`}>
      <a className="brand" href="/" aria-label="CS Gooner home">
        <span className="brand-mark"><img src="/favicon.svg" alt="" /></span>
        <span>CS <span className="brand-light">GOONER</span></span>
      </a>
      <nav id="main-nav" className="main-nav" aria-label="Main navigation"
        onClick={(event) => { if ((event.target as HTMLElement).closest("a")) setMenuOpen(false); }}>
        <a className={active("home")} href="/" aria-current={current("home")}>Matches</a>
        <a href={PREDICTOR_HASH}>Predictor</a>
        <a className={active("stats")} href={STATS_PATH} aria-current={current("stats")}>Stats</a>
        <a className={active("about")} href={ABOUT_PATH} aria-current={current("about")}>About</a>
        <a className={page === "account" || page === "export" ? "nav-active" : undefined}
          href={ACCOUNT_PATH} aria-current={page === "account" || page === "export" ? "page" : undefined}>Account</a>
      </nav>
      {page === "home" && <div className="live-indicator"><span /> MODEL ONLINE</div>}
      {steamUser && (
        <a className="account-avatar" href={ACCOUNT_PATH} aria-label="Account settings"
          title={steamUser.steam_id ?? "Account"}>
          {(steamUser.steam_id ?? "?").slice(-2)}
        </a>
      )}
      <button type="button" className="nav-toggle" aria-controls="main-nav" aria-expanded={menuOpen}
        aria-label={menuOpen ? "Close menu" : "Open menu"} onClick={() => setMenuOpen((open) => !open)}>
        <span aria-hidden="true" /><span aria-hidden="true" /><span aria-hidden="true" />
      </button>
    </header>
  );
}

export default App;
