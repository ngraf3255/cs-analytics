import { useMe, useSteamStatus } from "./hooks";

/** Short marketing hero for signed-out visitors on `/`. */
export function GuestHero() {
  const { enabled, loading } = useSteamStatus();
  const { me, checked } = useMe(enabled);
  // Hold until auth resolves so a signed-in reload never flashes guest chrome.
  if (loading || (enabled && !checked) || me) return null;
  return (
    <section className="hero">
      <div className="hero-copy">
        <h1>Every round<br />has a <em>turning point.</em></h1>
      </div>
      <div className="hero-grid" aria-hidden="true" />
    </section>
  );
}
