import { useState } from "react";
import { steamApi, type ExportTable } from "./api";
import { ApiError } from "./errors";
import "./tableauExport.css";

const GUIDE_URL = "https://github.com/ngraf3255/cs-analytics/blob/main/tableau/README.md#connect-tableau";

type Counts = { matches?: number; rounds?: number; personalMatches?: number };

const TABLES: { table: ExportTable; label: string; title: string; rows: (c: Counts) => number | undefined }[] = [
  { table: "rounds", label: "Rounds CSV", title: "Rounds", rows: (c) => c.rounds },
  { table: "matches", label: "Matches CSV", title: "Matches", rows: (c) => c.matches },
];

/** "Export for Tableau": downloads the signed-in user's rounds / matches as CSV
 * (GET /matches/export/{table}.csv; columns documented in tableau/README.md). Counts come from
 * the summary so people know what they'll get before downloading. */
export function TableauExport({ matches, rounds }: Counts = {}) {
  const [busy, setBusy] = useState<ExportTable | "both" | null>(null);
  const [status, setStatus] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const counts = { matches, rounds };

  async function download(tables: ExportTable[], key: ExportTable | "both") {
    setBusy(key);
    setStatus(null);
    const done: string[] = [];
    try {
      for (const table of tables) done.push(await steamApi.downloadExport(table));
      setStatus({ tone: "ok", text: `Downloaded ${done.join(" and ")}.` });
    } catch (reason) {
      const message = reason instanceof ApiError ? reason.message : "Something went wrong. Try again.";
      setStatus({ tone: "error", text: `${done.length ? `Downloaded ${done.join(" and ")}, then the next export failed.` : "Export failed."} ${message}` });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="tableau-export" role="group" aria-label="Export for Tableau">
      <span className="section-kicker">EXPORT FOR TABLEAU</span>
      <div className="export-options">
        {TABLES.map(({ table, label, title, rows }) => {
          const n = rows(counts);
          return (
            <div key={table} className="export-option">
              <strong>{title}{n != null && <small> · {n.toLocaleString()} row{n === 1 ? "" : "s"}</small>}</strong>
              <button type="button" className="ghost-button" disabled={busy !== null} onClick={() => void download([table], table)}>
                {busy === table ? "Preparing…" : label}
              </button>
            </div>
          );
        })}
      </div>
      <div className="tableau-export-buttons">
        <button type="button" className="ghost-button" disabled={busy !== null} onClick={() => void download(["rounds", "matches"], "both")}>
          {busy === "both" ? "Preparing…" : "Download both"}
        </button>
        <a className="export-guide" href={GUIDE_URL} target="_blank" rel="noreferrer">How to open them in Tableau ↗</a>
      </div>
      {status && <span className={status.tone === "ok" ? "steam-notice" : "steam-error"} role="status">{status.text}</span>}
    </div>
  );
}
