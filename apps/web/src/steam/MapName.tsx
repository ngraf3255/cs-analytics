import { mapLabel } from "./format";

/** Digits ("Rush 001") in DM Mono with slashed zeros so they never read as O's. */
export function mapDigits(name: string) {
  return name.split(/(\d+)/).map((part, i) => (/^\d+$/.test(part) ? <span key={i} className="map-digits" data-ui-number>{part}</span> : part));
}

/** Shared map-name render for every visible (non-<option>, non-canvas) map label. */
export function MapName({ map }: { map: string | null | undefined }) {
  return <>{mapDigits(mapLabel(map))}</>;
}
