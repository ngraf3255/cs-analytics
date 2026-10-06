import type { MatchReport, YouAnalytics } from "./types";
import { kdText, mapLabel, matchDate, resultText } from "./format";

/** What a share image shows: a title, a big headline, up to four stat tiles and a footer line. */
export type ShareCard = {
  kicker: string;
  title: string;
  headline: string;
  tone: "won" | "lost" | "neutral";
  stats: { label: string; value: string }[];
  footer: string;
  fileName: string;
};

const pct = (rate: number | null | undefined) => (rate == null ? "—" : `${Math.round(rate * 100)}%`);
const slug = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

/** One match, from the signed-in player's side when they are in the demo (else the match itself). */
export function matchCard(report: MatchReport): ShareCard {
  const { match } = report;
  const you = report.you?.status === "in_match" ? report.you : null;
  const map = mapLabel(match.map_name);
  const date = matchDate(match).day;
  const score = match.score ? `${Math.max(match.score.ct, match.score.t)}–${Math.min(match.score.ct, match.score.t)}` : "—";
  if (!you) {
    return {
      kicker: "MATCH REPORT", title: map, headline: score, tone: "neutral",
      stats: [
        { label: "ROUNDS", value: String(match.rounds_count) },
        { label: "MODEL CALLED", value: report.summary.scored ? `${report.summary.correct_predictions}/${report.summary.scored}` : "—" },
      ],
      footer: date, fileName: `csgooner-${slug(map)}-${slug(date)}.png`,
    };
  }
  const duels = you.opening_kills + you.opening_deaths;
  return {
    kicker: "MATCH REPORT", title: map, headline: resultText(you) ?? `${you.won} of ${you.rounds} rounds`,
    tone: you.result === "won" ? "won" : you.result === "lost" ? "lost" : "neutral",
    stats: [
      { label: "KILLS / DEATHS", value: `${you.kills} / ${you.deaths}` },
      { label: "K/D RATIO", value: kdText(you.kd) },
      { label: "ROUNDS WON", value: pct(you.win_rate) },
      { label: "OPENING DUELS", value: duels ? `${you.opening_kills}–${you.opening_deaths}` : "—" },
    ],
    footer: date, fileName: `csgooner-${slug(map)}-${slug(date)}.png`,
  };
}

/** The player's numbers across all their imported matches. */
export function profileCard(you: YouAnalytics): ShareCard {
  const r = you.results;
  return {
    kicker: "MY CS2 NUMBERS", title: `${you.matches} match${you.matches === 1 ? "" : "es"} · W–L`,
    headline: `${r.won}–${r.lost}${r.tied ? `–${r.tied}` : ""}`, tone: r.won > r.lost ? "won" : r.won < r.lost ? "lost" : "neutral",
    stats: [
      { label: "ROUNDS WON", value: pct(you.win_rate) },
      { label: "K/D", value: kdText(you.kd) },
      { label: "CT / T", value: `${pct(you.sides.ct.win_rate)} / ${pct(you.sides.t.win_rate)}` },
      { label: "OPENING DUELS", value: pct(you.opening_duels.win_rate) },
    ],
    footer: `${you.rounds} rounds`, fileName: "csgooner-my-numbers.png",
  };
}

const W = 1200, H = 630;
const COLORS = { bg: "#090d12", panel: "#10161d", line: "#29313a", text: "#f2f3f0", muted: "#9aa4ab", acid: "#c8f169", lost: "#ff9569" };

/** Draw the card on a 1200×630 canvas (the usual link-preview size) and return it as a PNG. */
export async function renderCard(card: ShareCard): Promise<Blob> {
  try { await document.fonts?.ready; } catch { /* fonts are a nicety */ }
  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas_unavailable");
  ctx.fillStyle = COLORS.bg; ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = "#ffffff08"; ctx.lineWidth = 1;
  for (let x = 0; x < W; x += 42) { ctx.beginPath(); ctx.moveTo(x + .5, 0); ctx.lineTo(x + .5, H); ctx.stroke(); }
  for (let y = 0; y < H; y += 42) { ctx.beginPath(); ctx.moveTo(0, y + .5); ctx.lineTo(W, y + .5); ctx.stroke(); }
  ctx.fillStyle = COLORS.acid; ctx.fillRect(0, 0, 8, H);

  const cond = "'Barlow Condensed', 'Arial Narrow', sans-serif", mono = "'DM Mono', ui-monospace, monospace";
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = COLORS.acid; ctx.font = `500 22px ${mono}`;
  ctx.fillText(card.kicker.split("").join(String.fromCharCode(8202)), 72, 96);
  ctx.fillStyle = COLORS.text; ctx.font = `800 64px ${cond}`;
  ctx.fillText(card.title.toUpperCase(), 72, 170, W - 144);
  ctx.fillStyle = card.tone === "won" ? COLORS.acid : card.tone === "lost" ? COLORS.lost : COLORS.text;
  ctx.font = `800 120px ${cond}`;
  ctx.fillText(card.headline.toUpperCase(), 72, 300, W - 144);

  const n = Math.max(card.stats.length, 1), gap = 12, top = 350, tileH = 140;
  const tileW = (W - 144 - gap * (n - 1)) / n;
  card.stats.forEach((s, i) => {
    const x = 72 + i * (tileW + gap);
    ctx.fillStyle = COLORS.panel; ctx.fillRect(x, top, tileW, tileH);
    ctx.strokeStyle = COLORS.line; ctx.strokeRect(x + .5, top + .5, tileW - 1, tileH - 1);
    ctx.fillStyle = COLORS.muted; ctx.font = `500 18px ${mono}`; ctx.fillText(s.label, x + 22, top + 42, tileW - 44);
    ctx.fillStyle = COLORS.text; ctx.font = `700 56px ${cond}`; ctx.fillText(s.value, x + 22, top + 110, tileW - 44);
  });

  ctx.fillStyle = COLORS.text; ctx.font = `800 30px ${cond}`; ctx.fillText("CS", 72, 572);
  ctx.fillStyle = "#b9c1c8"; ctx.font = `500 30px ${cond}`; ctx.fillText("GOONER", 112, 572);
  ctx.fillStyle = COLORS.muted; ctx.font = `400 20px ${mono}`;
  ctx.textAlign = "right"; ctx.fillText(`${card.footer} · csgooner.com`, W - 72, 572); ctx.textAlign = "left";

  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("canvas_unavailable"))), "image/png"));
}

/** Phone share sheet with the image when the browser supports file sharing, else a download. */
export async function shareCard(card: ShareCard): Promise<"shared" | "downloaded" | "cancelled"> {
  const blob = await renderCard(card);
  const file = new File([blob], card.fileName, { type: "image/png" });
  const nav = navigator as Navigator & { canShare?: (data: ShareData) => boolean };
  if (nav.share && nav.canShare?.({ files: [file] })) {
    try {
      await nav.share({ files: [file], title: `${card.title} · CS Gooner` });
      return "shared";
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return "cancelled";
      // fall through to a download (e.g. share sheet unavailable in this context)
    }
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = card.fileName;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return "downloaded";
}
