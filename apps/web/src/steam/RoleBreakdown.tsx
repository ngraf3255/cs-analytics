import { kdText } from "./format";
import type { SideRole, YouAnalytics } from "./types";

const pct = (rate: number | null | undefined) => (rate == null ? "—" : `${Math.round(rate * 100)}%`);

const ROLE_TEXT: Record<string, { name: string; hint: string }> = {
  entry: { name: "Entry", hint: "You take the first duel far more often than the 5v5 average." },
  support: { name: "Support", hint: "You rarely take the first duel; you play behind the opening." },
  balanced: { name: "Balanced", hint: "Your opening-duel share is close to the 5v5 average." },
};

/** Per-side role from opening-duel involvement (GET /matches/summary ``you.roles``). */
export function RoleBreakdown({ roles }: { roles: NonNullable<YouAnalytics["roles"]> }) {
  const baseline = roles.baseline_opening_attempt_rate;
  return (
    <div className="role-breakdown">
      <span className="section-kicker">YOUR ROLE BY SIDE · OPENING DUELS</span>
      <dl className="report-header summary-tiles" aria-label="Your role by side">
        <RoleTile side="CT" role={roles.ct} baseline={baseline} min={roles.min_rounds} />
        <RoleTile side="T" role={roles.t} baseline={baseline} min={roles.min_rounds} />
      </dl>
    </div>
  );
}

function RoleTile({ side, role, baseline, min }: { side: string; role: SideRole; baseline: number; min: number }) {
  const text = role.role ? ROLE_TEXT[role.role] : null;
  return (
    <div>
      <dt>AS {side}</dt>
      <dd>{text?.name ?? (role.role ? role.role : "—")}</dd>
      <small>
        {role.role
          ? `${text?.hint ?? ""} In ${pct(role.opening_attempt_rate)} of rounds (avg ${pct(baseline)}), won ${pct(role.opening_win_rate)} · K/D ${kdText(role.kd)} · survived ${pct(role.survival_rate)}`
          : `Needs ${min}+ rounds on ${side} (${role.rounds} so far).`}
      </small>
    </div>
  );
}
