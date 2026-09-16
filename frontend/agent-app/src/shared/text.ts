/** Split into first N words for compact timeline previews. */
export function firstWords(text: string, count = 8): string {
  const words = text.replace(/\s+/g, " ").trim().split(" ").filter(Boolean);
  if (!words.length) return "";
  const head = words.slice(0, count).join(" ");
  return words.length > count ? `${head}…` : head;
}
