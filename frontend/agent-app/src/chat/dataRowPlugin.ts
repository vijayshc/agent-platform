/** Group consecutive narrow (`half`/`third`/`quarter`) blocks into shared rows.
 *
 * react-markdown renders each block on its own, so narrow blocks would stack.
 * This rehype pass groups runs of adjacent narrow blocks in a `td-row`.
 * A run where every block shares the same narrow layout — and the run is
 * longer than one row — becomes a masonry column row (`td-row td-cols-N`):
 * its cards are dealt round-robin into N `.td-col` columns, so a short
 * card's neighbour slides up underneath it instead of waiting below the
 * tallest card in the row. Shorter runs and mixed-layout runs keep the
 * 12-column grid (half=6, third=4, quarter=3), where siblings stretch to
 * equal height. It transforms the AST react-markdown already built — it does
 * not parse markdown itself.
 */
import type { Element, ElementContent, Root, RootContent } from "hast";
import { blockLayout, type RichBlock } from "./toolDataTypes";
import { parseDataBlock } from "./dataBlock";

const FENCE_LANGUAGES = new Set([
  "language-chart",
  "language-table",
  "language-card",
  "language-list",
  "language-progress",
  "language-note",
]);

function textOf(node: RootContent): string {
  if (node.type === "text") return node.value;
  if (node.type === "element") return (node.children ?? []).map(textOf).join("");
  return "";
}

/** The placeholder text a dashboard block node carries, or `null`. */
function blockText(node: RootContent): string | null {
  if (node.type !== "element") return null;
  // A bare `#TABLE_D1` / `#NOTE` line is a paragraph.
  if (node.tagName === "p") return textOf(node);
  // A fenced block body is a <pre><code class="language-…">.
  if (node.tagName === "pre") {
    const code = (node.children ?? []).find(
      (child): child is Element => child.type === "element" && child.tagName === "code",
    );
    if (!code) return null;
    const classes = code.properties?.className;
    const languages = Array.isArray(classes) ? classes : [];
    if (!languages.some((language) => FENCE_LANGUAGES.has(String(language)))) {
      return null;
    }
    return textOf(code);
  }
  return null;
}

function narrowBlock(node: RootContent): RichBlock | null {
  const text = blockText(node);
  if (text == null) return null;
  const block = parseDataBlock(text);
  if (!block) return null;
  return blockLayout(block) === "full" ? null : block;
}

/** A rehype plugin that wraps each run of adjacent narrow blocks in a `.td-row`. */
export function rehypePairDataRows() {
  return (tree: Root): void => {
    const children = tree.children;
    if (!children?.length) return;
    const grouped: RootContent[] = [];
    let index = 0;
    while (index < children.length) {
      if (!narrowBlock(children[index])) {
        grouped.push(children[index]);
        index += 1;
        continue;
      }
      // Collect the run, skipping whitespace text nodes between blocks.
      const run: ElementContent[] = [];
      const layouts: string[] = [];
      while (index < children.length) {
        const current = children[index];
        if (isWhitespace(current)) {
          index += 1;
          continue;
        }
        const block = narrowBlock(current);
        if (!block) break;
        run.push(current as ElementContent);
        layouts.push(blockLayout(block));
        index += 1;
      }
      if (run.length === 1) {
        grouped.push(run[0]);
        continue;
      }
      // A uniform run packs as masonry columns — but only when a column would
      // hold more than one card. A run that fills exactly one row (e.g. two
      // halves) keeps the grid so siblings stretch to equal height.
      const uniform = layouts[0];
      const cols =
        layouts.every((l) => l === uniform) && uniform !== "full"
          ? { half: 2, third: 3, quarter: 4 }[uniform] ?? 0
          : 0;
      if (cols > 0 && run.length > cols) {
        const columns: Element[] = Array.from({ length: cols }, () => ({
          type: "element",
          tagName: "div",
          properties: { className: ["td-col"] },
          children: [] as ElementContent[],
        }));
        run.forEach((node, i) => {
          (columns[i % cols] as Element).children.push(node);
        });
        grouped.push({
          type: "element",
          tagName: "div",
          properties: { className: ["td-row", "td-cols", `td-cols-${cols}`] },
          children: columns,
        });
        continue;
      }
      const row: Element = {
        type: "element",
        tagName: "div",
        properties: { className: ["td-row"] },
        children: run,
      };
      grouped.push(row);
    }
    tree.children = grouped;
  };
}

function isWhitespace(node: RootContent): boolean {
  return node.type === "text" && node.value.trim() === "";
}
