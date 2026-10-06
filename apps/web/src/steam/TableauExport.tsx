import { useEffect, useId, useRef, useState } from "react";
import { steamApi, type ExportTable } from "./api";
import { ApiError } from "./errors";
import "./tableauExport.css";

const GUIDE_URL = "https://github.com/ngraf3255/cs-analytics/blob/main/tableau/README.md#connect-tableau";

type Counts = { matches?: number; rounds?: number; personalMatches?: number };

const TABLES: { table: ExportTable; label: string }[] = [
  { table: "rounds", label: "Rounds CSV" },
  { table: "matches", label: "Matches CSV" },
];

type Props = Counts & {
  /** ``menu`` (default): compact ⋮. ``page``: plain action list for ``/account/export``. */
  layout?: "menu" | "page";
};

/** Download rounds / matches CSV for Tableau (GET /matches/export/{table}.csv). */
export function TableauExport({ layout = "menu" }: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<ExportTable | "both" | null>(null);
  const [status, setStatus] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open || layout !== "menu") return;
    const onPointer = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, layout]);

  async function download(tables: ExportTable[], key: ExportTable | "both") {
    setBusy(key);
    setStatus(null);
    setOpen(false);
    const done: string[] = [];
    try {
      for (const table of tables) done.push(await steamApi.downloadExport(table));
      setStatus({ tone: "ok", text: `Downloaded ${done.join(" and ")}.` });
    } catch (reason) {
      const message = reason instanceof ApiError ? reason.message : "Something went wrong. Try again.";
      setStatus({
        tone: "error",
        text: `${done.length ? `Downloaded ${done.join(" and ")}, then the next export failed.` : "Export failed."} ${message}`,
      });
    } finally {
      setBusy(null);
    }
  }

  const statusEl = status && (
    <span className={status.tone === "ok" ? "steam-notice export-status" : "steam-error export-status"} role="status">
      {status.text}
    </span>
  );

  if (layout === "page") {
    return (
      <div className="tableau-export tableau-export-page" role="group" aria-label="Export for Tableau">
        <div className="export-page-actions">
          {TABLES.map(({ table, label }) => (
            <button
              key={table}
              type="button"
              className="ghost-button"
              disabled={busy !== null}
              onClick={() => void download([table], table)}
            >
              {busy === table ? "Preparing…" : label}
            </button>
          ))}
          <button
            type="button"
            className="ghost-button"
            disabled={busy !== null}
            onClick={() => void download(["rounds", "matches"], "both")}
          >
            {busy === "both" ? "Preparing…" : "Download both"}
          </button>
        </div>
        <a className="export-guide-link" href={GUIDE_URL} target="_blank" rel="noreferrer">
          Tableau help ↗
        </a>
        {statusEl}
      </div>
    );
  }

  return (
    <div className="tableau-export" ref={rootRef} role="group" aria-label="Export for Tableau">
      <button
        type="button"
        className="export-menu-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={open ? "Close export menu" : "Export menu"}
        disabled={busy !== null}
        onClick={() => setOpen((v) => !v)}
      >
        {busy !== null ? "…" : "⋮"}
      </button>
      {open && (
        <div className="export-menu" id={menuId} role="menu" aria-label="Export for Tableau">
          {TABLES.map(({ table, label }) => (
            <button
              key={table}
              type="button"
              role="menuitem"
              className="export-menu-item"
              disabled={busy !== null}
              onClick={() => void download([table], table)}
            >
              {busy === table ? "Preparing…" : label}
            </button>
          ))}
          <button
            type="button"
            role="menuitem"
            className="export-menu-item"
            disabled={busy !== null}
            onClick={() => void download(["rounds", "matches"], "both")}
          >
            {busy === "both" ? "Preparing…" : "Download both"}
          </button>
          <a
            className="export-menu-item export-guide"
            role="menuitem"
            href={GUIDE_URL}
            target="_blank"
            rel="noreferrer"
            onClick={() => setOpen(false)}
          >
            Tableau help ↗
          </a>
        </div>
      )}
      {statusEl}
    </div>
  );
}
