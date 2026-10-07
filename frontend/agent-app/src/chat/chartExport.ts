/** Download a rendered chat chart as a PNG.
 *
 * The card that wraps a chart already carries everything a reader needs — its
 * title, subtitle, axis labels, legend and the plot itself. Rather than
 * re-drawing a chart from the spec (which would drift from what is on screen),
 * the whole card is captured as it is rendered, so the downloaded image is
 * exactly the chart the user is looking at, title and labels included.
 */
import { toBlob } from "html-to-image";

/** A filesystem-safe file name for a chart title. */
export function chartFileName(title: string, extension = "png"): string {
  const slug = title
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60)
    .replace(/-+$/g, "");
  return `${slug || "chart"}.${extension}`;
}

/** The card's own surface colour, so the PNG has no transparent corners. */
function surfaceColor(node: HTMLElement): string {
  const background = getComputedStyle(node).backgroundColor;
  return background && background !== "rgba(0, 0, 0, 0)" ? background : "#ffffff";
}

function triggerDownload(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoke on the next tick: the click has already handed the blob to the
  // browser, and revoking synchronously can cancel the download in some builds.
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * Capture ``node`` (a chart card) to a PNG and download it. The action controls
 * themselves are excluded so the exported image contains only the chart.
 */
export async function downloadChartPng(node: HTMLElement, title: string): Promise<void> {
  const blob = await toBlob(node, {
    // Retina-sharp output, independent of the display's device pixel ratio.
    pixelRatio: 2,
    backgroundColor: surfaceColor(node),
    // Chart text is drawn with the app's system font stack; embedding fonts is
    // unnecessary work and a source of cross-origin stylesheet failures.
    skipFonts: true,
    // The card carries `margin: 10px 0`, which html-to-image would otherwise
    // keep on the clone and use to shift it — leaving the top border floating
    // and the bottom edge clipped. Zero it, and drop the outer border so every
    // edge of the exported image is consistent (none of the card frame shows).
    // The logical shorthands must be cleared too: the clone copies
    // `border-inline`/`border-block` from the computed style, and `border`
    // alone does not reset them.
    style: {
      margin: "0",
      border: "none",
      borderBlock: "none",
      borderInline: "none",
      borderRadius: "0",
    },
    filter: (child) => (child as HTMLElement).dataset?.exportSkip !== "true",
  });
  if (!blob) throw new Error("The browser could not render the chart to an image.");
  triggerDownload(blob, chartFileName(title));
}
