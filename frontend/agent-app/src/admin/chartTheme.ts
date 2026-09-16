import { useEffect, useMemo, useState } from "react";

/* ------------------------------------------------------------------ *
 * Theme-aware chart palette.
 *
 * Charts must feel at home in dark / light / lightColored themes. We
 * read the app's CSS variables for text + grid surfaces so axis labels,
 * tooltips and grid lines follow the active theme, while the *data*
 * colors come from a curated, modern categorical palette below that
 * reads as a cohesive brand rather than browser-default primaries.
 * ------------------------------------------------------------------ */

export interface ChartPalette {
  text: string;
  muted: string;
  grid: string;
  card: string;
  tooltipBg: string;
  tooltipBorder: string;
  /** Primary accent (area/line/bar color for the main trend charts). */
  primary: string;
  /** Curated categorical palette for breakdowns. */
  series: string[];
}

function readVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/**
 * Curated, modern categorical palette. Harmonious hue progression that
 * works on both dark and light surfaces (high enough luminance contrast).
 */
const CATEGORICAL = [
  "#6366f1", // indigo
  "#06b6d4", // cyan
  "#10b981", // emerald
  "#f59e0b", // amber
  "#f43f5e", // rose
  "#8b5cf6", // violet
  "#0ea5e9", // sky
  "#14b8a6", // teal
];

/**
 * Return a palette derived from the current theme. Re-reads theme
 * variables whenever the theme class on <body>/<html> changes, so charts
 * re-render with the correct surface colors after a theme switch.
 */
export function useChartPalette(): ChartPalette {
  const [themeClass, setThemeClass] = useState<string>(() => document.body.className);
  useEffect(() => {
    const update = () => setThemeClass(document.body.className);
    const mo = new MutationObserver(update);
    mo.observe(document.body, { attributes: true, attributeFilter: ["class"] });
    const moHtml = new MutationObserver(update);
    moHtml.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => {
      mo.disconnect();
      moHtml.disconnect();
    };
  }, []);

  return useMemo(() => {
    void themeClass; // dependency: recompute on theme change
    const text = readVar("--text-secondary", "#b0b0b0");
    const muted = readVar("--text-muted", "#808080");
    const grid = readVar("--border-color", "#2d2d2d");
    const card = readVar("--card-bg", "#1f1f1f");
    const tooltipBg = readVar("--card-bg", "#1f1f1f");
    const tooltipBorder = readVar("--border-color", "#2d2d2d");
    const primary = readVar("--info-color", "#3b82f6");
    return { text, muted, grid, card, tooltipBg, tooltipBorder, primary, series: CATEGORICAL };
  }, [themeClass]);
}

/** Shared axis / tooltip / grid styling objects for Recharts. */
export function chartAxisProps(p: ChartPalette) {
  return {
    axisLine: { stroke: p.grid },
    tickLine: { stroke: "transparent" },
    tick: { fill: p.muted, fontSize: 11 },
  };
}

export function chartGridProps(p: ChartPalette) {
  return { stroke: p.grid, strokeDasharray: "3 3", vertical: false };
}

/** Number formatting helpers. */
export function compact(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

/** Format a ``YYYY-MM-DD`` day label for chart axes (short). */
export function shortDay(dayIso: string): string {
  const d = new Date(dayIso + "T00:00:00");
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
