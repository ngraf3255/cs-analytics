import type { SideRole, YouAnalytics } from "./types";

const pct = (rate: number | null | undefined) => (rate == null ? "—" : `${Math.round(rate * 100)}%`);

const ROLE_NAME: Record<string, string> = {
  entry: "Entry",
  support: "Support",
  balanced: "Balanced",
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
  const name = role.role ? ROLE_NAME[role.role] : null;
  return (
    <div>
      <dt>AS {side}</dt>
      <dd>{name ?? (role.role ? role.role : "—")}</dd>
      <small>
        {role.role
          ? `In ${pct(role.opening_attempt_rate)} of rounds (avg ${pct(baseline)})`
          : `Needs ${min}+ rounds on ${side} (${role.rounds} so far).`}
      </small>
    </div>
  );
}
