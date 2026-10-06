import { useEffect, useState } from "react";
import { steamApi } from "./api";
import { kdText } from "./format";
import type { PeerCompare as Compare, PeerComparison, PeerGroup } from "./types";

const pct = (rate: number | null | undefined) => (rate == null ? "—" : `${Math.round(rate * 100)}%`);
const num = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));
const mapLabel = (map: string | null) => (map ? map.replace(/^de_/, "").replaceAll("_", " ") : "Unknown map");

type Metric = { key: keyof PeerGroup; label: string; fmt: (v: number | null) => string };
const METRICS: Metric[] = [
  { key: "kills_per_round", label: "Kills / round", fmt: num },
  { key: "kd", label: "K/D", fmt: kdText },
  { key: "round_win_rate", label: "Rounds won", fmt: pct },
  { key: "survival_rate", label: "Survived", fmt: pct },
  { key: "opening_win_rate", label: "Opening duels won", fmt: pct },
];

function ordinal(n: number) {
  return n >= 50 ? `Top ${Math.max(1, 100 - n)}%` : `Bottom ${Math.max(1, n)}%`;
}

/** You vs the other players in your own lobbies (GET /matches/peers), overall and per map.
 * Hidden on older APIs, for guests, and until you're in a match. */
export function PeerCompare({ refreshKey }: { refreshKey: number }) {
  const [data, setData] = useState<PeerComparison | null>(null);
  const [map, setMap] = useState<string>("__all");

  useEffect(() => {
    let cancelled = false;
    steamApi.getPeers().then((body) => { if (!cancelled) setData(body); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [refreshKey]);

  if (!data || !data.overall.you || !data.overall.peers) return null;
  const current: Compare = map === "__all" ? data.overall : data.maps.find((m) => (m.map_name ?? "") === map) ?? data.overall;
  return (
    <section className="peer-compare" aria-label="Compare to your lobbies">
      <div className="peer-head">
        <span className="section-kicker">YOU VS YOUR LOBBIES</span>
        {data.maps.length > 1 && (
          <span className="select-wrap peer-map">
            <select aria-label="Map to compare" value={map} onChange={(e) => setMap(e.target.value)}>
              <option value="__all">All maps</option>
              {data.maps.map((m) => <option key={m.map_name ?? "?"} value={m.map_name ?? ""}>{mapLabel(m.map_name)} ({m.matches})</option>)}
            </select><span className="chevron">⌄</span>
          </span>
        )}
      </div>
      {current.percentiles.kills_per_round != null && (
        <p className="peer-headline">
          <strong>{ordinal(current.percentiles.kills_per_round)}</strong> for kills per round
          {current.percentiles.kd != null && <> · <strong>{ordinal(current.percentiles.kd)}</strong> for K/D</>}
          {" "}among {current.peers?.lines ?? 0} player-matches.
        </p>
      )}
      <table className="round-table peer-table" aria-label="You vs lobby average">
        <thead><tr><th>Stat</th><th>You</th><th>Lobby avg</th><th>Gap</th></tr></thead>
        <tbody>
          {METRICS.map((m) => {
            const you = current.you?.[m.key] as number | null | undefined;
            const peer = current.peers?.[m.key] as number | null | undefined;
            const gap = you != null && peer != null ? you - peer : null;
            return (
              <tr key={m.key}>
                <td>{m.label}</td>
                <td>{m.fmt(you ?? null)}</td>
                <td>{m.fmt(peer ?? null)}</td>
                <td className={gap == null || Math.abs(gap) < 1e-9 ? "" : gap > 0 ? "you-won" : "you-lost"}>
                  {gap == null ? "—" : `${gap > 0 ? "+" : "−"}${m.fmt === pct ? `${Math.round(Math.abs(gap) * 100)} pts` : Math.abs(gap).toFixed(2)}`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="steam-muted peer-note">
        Peers are the other players in your own imported matches. Matchmaking groups similar skill, but ranks aren’t stored, so this isn’t a same-rank comparison.
        Percentiles need {data.min_peer_lines}+ peer player-matches.
      </p>
    </section>
  );
}
