import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";

/* ------------------------------------------------------------------ *
 * SearchableSelect — a themed single-select combobox.
 *
 * A native <select> cannot be searched by typing, and the option lists
 * here (vector collections) are long enough that scrolling is poor UX.
 * Keyboard: typing filters, Enter picks the first match, Escape closes.
 * ------------------------------------------------------------------ */

export interface SearchableOption {
  value: string;
  label: string;
  hint?: string;
}

interface SearchableSelectProps {
  options: SearchableOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyLabel?: string;
  disabled?: boolean;
  testId?: string;
}

export function SearchableSelect({
  options,
  value,
  onChange,
  placeholder = "Select…",
  searchPlaceholder = "Search…",
  emptyLabel = "No matches",
  disabled = false,
  testId,
}: SearchableSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return options;
    return options.filter((option) => option.label.toLowerCase().includes(needle));
  }, [options, query]);

  const current = options.find((option) => option.value === value);

  const choose = (next: string) => {
    onChange(next);
    setOpen(false);
    setQuery("");
  };

  return (
    <div className="aa-ss" ref={rootRef}>
      <button
        type="button"
        className="aa-ss-trigger"
        onClick={() => !disabled && setOpen((prev) => !prev)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        data-testid={testId}
      >
        <span className={current ? "aa-ss-value" : "aa-ss-value aa-muted"}>{current?.label || placeholder}</span>
        <ChevronDown size={14} />
      </button>

      {open && (
        <div className="aa-ss-menu" role="listbox">
          <div className="aa-ss-search">
            <Search size={13} className="aa-ss-search-icon" />
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && filtered.length > 0) {
                  event.preventDefault();
                  choose(filtered[0].value);
                }
              }}
              placeholder={searchPlaceholder}
              aria-label={searchPlaceholder}
            />
          </div>
          <div className="aa-ss-list">
            {filtered.map((option) => (
              <button
                key={option.value}
                type="button"
                role="option"
                aria-selected={option.value === value}
                className={`aa-ss-option${option.value === value ? " active" : ""}`}
                onClick={() => choose(option.value)}
              >
                <span className="aa-ss-option-copy">
                  <span className="aa-ss-option-label">{option.label}</span>
                  {option.hint && <span className="aa-ss-option-hint">{option.hint}</span>}
                </span>
                {option.value === value && <Check size={14} />}
              </button>
            ))}
            {filtered.length === 0 && <div className="aa-ss-empty">{emptyLabel}</div>}
          </div>
        </div>
      )}
    </div>
  );
}
