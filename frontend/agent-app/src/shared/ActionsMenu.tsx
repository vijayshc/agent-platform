import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown } from "lucide-react";

export interface ActionItem {
  key: string;
  label: string;
  /** Handler for a plain action. Ignored when `href` is set. */
  onSelect?: () => void;
  /**
   * Renders the item as a real anchor so link actions keep native browser
   * behaviour (same-tab navigation, middle-click, "Open in new tab").
   */
  href?: string;
  disabled?: boolean;
  title?: string;
  danger?: boolean;
  testId?: string;
}

const MENU_WIDTH = 184;
const MENU_GAP = 6;
const EDGE = 8;
const ITEM_HEIGHT = 36;

/**
 * A compact "Actions" trigger that opens a portal-rendered dropdown. The menu
 * is portaled to <body> so it is never clipped by overflow containers such as
 * table cells or scroll wrappers.
 */
export function ActionsMenu({
  items,
  label = "Actions",
  testId,
}: {
  items: ActionItem[];
  label?: string;
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  function place() {
    const el = triggerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const width = Math.max(MENU_WIDTH, rect.width);
    const left = Math.max(EDGE, Math.min(rect.right - width, window.innerWidth - width - EDGE));
    const height = items.length * ITEM_HEIGHT + 12;
    let top = rect.bottom + MENU_GAP;
    if (top + height > window.innerHeight - EDGE) {
      top = Math.max(EDGE, rect.top - MENU_GAP - height);
    }
    setPos({ top, left, width });
  }

  useEffect(() => {
    if (!open) return;
    function onDocDown(e: MouseEvent) {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    function onReflow() {
      setOpen(false);
    }
    document.addEventListener("mousedown", onDocDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onReflow);
    window.addEventListener("scroll", onReflow, true);
    return () => {
      document.removeEventListener("mousedown", onDocDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onReflow);
      window.removeEventListener("scroll", onReflow, true);
    };
  }, [open]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="aa-btn aa-actions-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid={testId}
        onClick={() => {
          if (!open) place();
          setOpen((v) => !v);
        }}
      >
        {label}
        <ChevronDown size={13} strokeWidth={2.4} className="aa-actions-caret" />
      </button>
      {open && pos
        ? createPortal(
            <div
              ref={menuRef}
              className="aa-actions-menu"
              role="menu"
              style={{ top: pos.top, left: pos.left, width: pos.width }}
            >
              {items.map((item) => {
                const className = `aa-actions-item${item.danger ? " danger" : ""}`;
                if (item.href && !item.disabled) {
                  return (
                    <a
                      key={item.key}
                      role="menuitem"
                      className={className}
                      href={item.href}
                      title={item.title}
                      data-testid={item.testId}
                      onClick={() => setOpen(false)}
                    >
                      {item.label}
                    </a>
                  );
                }
                return (
                  <button
                    key={item.key}
                    type="button"
                    role="menuitem"
                    className={className}
                    disabled={item.disabled}
                    title={item.title}
                    data-testid={item.testId}
                    onClick={() => {
                      setOpen(false);
                      item.onSelect?.();
                    }}
                  >
                    {item.label}
                  </button>
                );
              })}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
