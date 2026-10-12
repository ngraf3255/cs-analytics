import type React from "react";
/** Small stroke icons (1em, currentColor). They replace text glyphs (⌄ ⓘ ✕ ↑ ▸ …), which can't be
 * contrast-checked and render differently per font. Always decorative: the control carries the name. */
const PATHS = {
  chevronDown: "M4 6l4 4 4-4",
  arrowUpRight: "M4.5 11.5l7-7M6 4.5h5.5V10",
  chevronRight: "M6 4l4 4-4 4",
  info: "M8 7.2v4.3M8 4.6v.1",
  close: "M4.5 4.5l7 7M11.5 4.5l-7 7",
  arrowUp: "M8 13V3.5M4 7.5l4-4 4 4",
  refresh: "M12.5 6A4.8 4.8 0 1 0 13 9.5M12.8 2.8V6H9.6",
  ban: "M3.8 12.2l8.4-8.4",
  warn: "M8 2.5l6 10.5H2zM8 6.5v3M8 11.4v.1",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg className={`icon${className ? ` ${className}` : ""}`} viewBox="0 0 16 16" width="1em" height="1em"
      aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round">
      {(name === "info" || name === "ban") && <circle cx="8" cy="8" r="6.2" />}
      <path d={PATHS[name]} />
    </svg>
  );
}

/** Text + trailing icon with one shared gap (flex buttons drop a plain trailing space). */
export function IconText({ children, icon = "arrowUpRight" }: { children: React.ReactNode; icon?: IconName }) {
  return <span className="icon-text">{children}<Icon name={icon} /></span>;
}

/** A text tooltip for an icon-only control: give the control aria-describedby={id} and wrap both
 * in .has-tip. Shown on hover and keyboard focus. */
export function Tip({ id, children }: { id: string; children: string }) {
  return <span role="tooltip" id={id} className="icon-tip">{children}</span>;
}
