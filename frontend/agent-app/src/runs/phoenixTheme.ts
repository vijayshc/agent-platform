/** Map the host app theme onto Phoenix's light/dark modes. */

const APP_THEME_KEY = "selectedTheme";
const PHOENIX_THEME_KEY = "arize-phoenix-theme";

export type PhoenixTheme = "light" | "dark";

/** The persisted choice wins: a stale per-tab value must not override it (the
 *  same precedence the app's theme preload now uses). */
export function readAppTheme(): string {
  try {
    const stored = localStorage.getItem(APP_THEME_KEY) || sessionStorage.getItem(APP_THEME_KEY);
    return stored || "light";
  } catch {
    return "light";
  }
}

/** Phoenix only has light/dark. Our lightColored theme maps to light. */
export function appThemeToPhoenix(appTheme: string = readAppTheme()): PhoenixTheme {
  return appTheme === "dark" ? "dark" : "light";
}

export function syncPhoenixThemeFromApp(): PhoenixTheme {
  const phoenix = appThemeToPhoenix();
  try {
    localStorage.setItem(PHOENIX_THEME_KEY, phoenix);
  } catch {
    /* storage may be blocked */
  }
  return phoenix;
}
