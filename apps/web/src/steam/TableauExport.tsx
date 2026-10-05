import { useState } from "react";
import { steamApi, type ExportTable } from "./api";
import { ApiError } from "./errors";

const TABLES: { table: ExportTable; label: string }[] = [
  { table: "rounds", label: "Rounds CSV" },
  { table: "matches", label: "Matches CSV" },
];

/** "Export for Tableau": downloads the signed-in user's rounds / matches as CSV
 * (GET /matches/export/{table}.csv; columns documented in tableau/README.md). */
export function TableauExport() {
  const [busy, setBusy] = useState<ExportTable | null>(null);
  const [status, setStatus] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  async function download(table: ExportTable) {
    setBusy(table);
    setStatus(null);
    try {
      const filename = await steamApi.downloadExport(table);
      setStatus({ tone: "ok", text: `Downloaded ${filename}.` });
    } catch (reason) {
      const message = reason instanceof ApiError ? reason.message : "Something went wrong. Try again.";
      setStatus({ tone: "error", text: `Export failed. ${message}` });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="tableau-export" role="group" aria-label="Export for Tableau">
      <span className="section-kicker">EXPORT FOR TABLEAU</span>
      <div className="tableau-export-buttons">
        {TABLES.map(({ table, label }) => (
          <button key={table} type="button" className="ghost-button" disabled={busy !== null} onClick={() => void download(table)}>
            {busy === table ? "Preparing…" : label}
          </button>
        ))}
      </div>
      <small className="steam-muted">
        One row per round (your side, kills, deaths, opening duel, the model’s CT win chance) or per match, for your
        matches only. Warmup and knife rounds aren’t included. Open the files in Tableau with Connect → Text file.
      </small>
      {status && <span className={status.tone === "ok" ? "steam-notice" : "steam-error"} role="status">{status.text}</span>}
    </div>
  );
}
