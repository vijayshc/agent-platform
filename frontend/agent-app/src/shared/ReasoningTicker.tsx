import { useLayoutEffect, useRef } from "react";
import "./reasoningTicker.css";

/** One-line live reasoning ticker.
 *
 * New deltas append at the right; once the text overflows, the latest text
 * stays visible and the oldest words slide off through the left-side shadow
 * mask. The component never wraps and never grows the layout.
 */
export function ReasoningTicker({ text, label = "Thinking" }: { text: string; label?: string }) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const textRef = useRef<HTMLSpanElement | null>(null);

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    const node = textRef.current;
    if (!viewport || !node) return;
    const overflow = Math.max(0, node.scrollWidth - viewport.clientWidth);
    node.style.transform = overflow > 0 ? `translateX(-${overflow}px)` : "translateX(0)";
  }, [text]);

  return (
    <div className="aa-think" data-testid="reasoning-line" role="status" aria-live="polite">
      <span className="aa-think-label">{label}</span>
      <div className="aa-think-viewport" ref={viewportRef}>
        <span className="aa-think-text" ref={textRef}>
          {text || "…"}
        </span>
      </div>
    </div>
  );
}
