import { useLayoutEffect, useRef } from "react";
import { StreamingMarkdown } from "./StreamingMarkdown";
import { hasRichBlocks } from "./chartProtocol";
import { ToolDataProvider } from "./toolDataContext";
import type { ToolDataPayload } from "./toolDataTypes";
import "./chatStream.css";

function visibleText(value?: string): string {
  return value && value.trim() ? value : "";
}

export function BubbleBody({
  content,
  prevContent,
  streaming,
  toolData,
  onSettle,
}: {
  content: string;
  prevContent?: string;
  streaming?: boolean;
  toolData?: ToolDataPayload[];
  onSettle?: () => void;
}) {
  const stageRef = useRef<HTMLDivElement>(null);
  const prevRef = useRef<HTMLDivElement>(null);
  const curRef = useRef<HTMLDivElement>(null);
  const onSettleRef = useRef(onSettle);
  const settledRef = useRef(false);
  onSettleRef.current = onSettle;

  const prev = visibleText(prevContent);
  const cur = content || "";
  const swapping = Boolean(prev && cur);
  const showCur = Boolean(cur) || (Boolean(streaming) && !prev);

  useLayoutEffect(() => {
    const stage = stageRef.current;
    const prevEl = prevRef.current;
    const curEl = curRef.current;
    if (!swapping || !stage || !prevEl || !curEl) {
      prevRef.current?.classList.remove("aa-prev-out");
      if (stage) {
        stage.style.height = "";
        stage.style.transition = "";
        stage.classList.remove("aa-stage-lock");
      }
      return;
    }

    settledRef.current = false;

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      if (!settledRef.current) {
        settledRef.current = true;
        onSettleRef.current?.();
      }
      return;
    }

    const prevH = prevEl.offsetHeight;
    const curH = curEl.offsetHeight;
    stage.style.transition = "none";
    stage.style.height = `${prevH}px`;
    stage.classList.add("aa-stage-lock");
    void stage.offsetHeight;

    prevEl.classList.add("aa-prev-out");
    stage.style.transition = "height var(--aa-height-duration) var(--aa-ease, ease)";
    stage.style.height = `${curH}px`;

    const ro = new ResizeObserver(() => {
      if (!stageRef.current || !curRef.current) return;
      stageRef.current.style.height = `${curRef.current.offsetHeight}px`;
    });
    ro.observe(curEl);

    const onEnd = (e: AnimationEvent) => {
      if (e.target !== prevEl) return;
      if (e.animationName && e.animationName !== "aa-prev-out") return;
      if (settledRef.current) return;
      settledRef.current = true;
      onSettleRef.current?.();
    };
    prevEl.addEventListener("animationend", onEnd);

    return () => {
      ro.disconnect();
      prevEl.removeEventListener("animationend", onEnd);
    };
  }, [swapping, prev]);

  return (
    <ToolDataProvider items={toolData} pending={streaming}>
      <div
        className={`aa-bubble-body aa-md${streaming ? " aa-streaming" : ""}${
          hasRichBlocks(cur) || hasRichBlocks(prev) ? " aa-rich" : ""
        }`}
      >
        <div className="aa-swap-stage" ref={stageRef}>
          {prev ? (
            <div key={prev} className="aa-bubble-prev" ref={prevRef} aria-hidden={swapping || undefined}>
              <StreamingMarkdown content={prev} />
            </div>
          ) : null}
          {showCur ? (
            <div className="aa-bubble-cur" ref={curRef}>
              <StreamingMarkdown content={cur} streaming={streaming} />
            </div>
          ) : null}
        </div>
      </div>
    </ToolDataProvider>
  );
}
