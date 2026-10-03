import { MagnifyingGlass, Minus, X } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";

import type { Filters, Scope } from "../types";

/** How long to wait after the last keystroke. Long enough that typing a word
 * is one request rather than six, short enough to feel like the results are
 * following you. */
const DEBOUNCE_MS = 250;

interface Props {
  filters: Filters;
  total: number;
  loading: boolean;
  update: (patch: Partial<Filters>) => void;
}

/** The search box and the two modifiers that belong to it.
 *
 * Scope and exclude used to live in the sidebar among twenty other controls,
 * which made them filters. They are not: they change what THIS query means,
 * and they are only ever reached for while typing one.
 */
export function SearchBar({ filters, total, loading, update }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(filters.q);
  const [showExclude, setShowExclude] = useState(Boolean(filters.exclude));

  // The URL is the source of truth, so a back button or a saved search has to
  // be able to overwrite what is in the box.
  useEffect(() => setDraft(filters.q), [filters.q]);

  useEffect(() => {
    if (draft === filters.q) return;
    const timer = setTimeout(() => update({ q: draft }), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, filters.q, update]);

  // "/" focuses the search from anywhere, the way every tool that is mostly
  // searching does. Ignored while typing somewhere else.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
      if (event.key === "/" && !typing) {
        event.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="searchbar">
      <div className="search-field">
        <MagnifyingGlass size={15} className="search-icon" aria-hidden="true" />
        <input
          ref={inputRef}
          className="search"
          type="text"
          placeholder={filters.scope === "title" ? "Search job titles…" : "Search titles and descriptions…"}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setDraft("");
              update({ q: "" });
            }
          }}
        />
        {draft && (
          <button type="button" className="search-clear" title="Clear (Esc)" onClick={() => { setDraft(""); update({ q: "" }); }}>
            <X size={13} weight="bold" />
          </button>
        )}
        {!draft && <kbd className="search-hint">/</kbd>}
      </div>

      <div className="search-scope" role="group" aria-label="Search scope">
        {([
          { value: "all", label: "Everything" },
          { value: "title", label: "Titles" },
        ] as { value: Scope; label: string }[]).map((option) => (
          <button
            key={option.value}
            type="button"
            className={filters.scope === option.value ? "on" : ""}
            onClick={() => update({ scope: option.value })}
          >
            {option.label}
          </button>
        ))}
      </div>

      <button
        type="button"
        className={`search-more${filters.exclude ? " on" : ""}`}
        title="Words that must NOT appear"
        onClick={() => {
          if (showExclude && filters.exclude) update({ exclude: "" });
          setShowExclude(!showExclude);
        }}
      >
        <Minus size={13} weight="bold" /> exclude
      </button>

      {showExclude && (
        <input
          className="search exclude"
          type="text"
          placeholder="without these words…"
          autoFocus
          defaultValue={filters.exclude}
          onBlur={(event) => event.target.value !== filters.exclude && update({ exclude: event.target.value })}
          onKeyDown={(event) => {
            if (event.key === "Enter") update({ exclude: (event.target as HTMLInputElement).value });
            if (event.key === "Escape") { update({ exclude: "" }); setShowExclude(false); }
          }}
        />
      )}

      {/* Sighted users see this count update as they type; without a live
          region a screen reader user gets no indication the result set
          changed at all. */}
      <span className={`search-count${loading ? " loading" : ""}`} aria-live="polite" aria-atomic="true">
        {loading ? "searching…" : `${total.toLocaleString()} jobs`}
      </span>
    </div>
  );
}
