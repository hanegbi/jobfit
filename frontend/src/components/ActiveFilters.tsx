import { X } from "@phosphor-icons/react";

import { EMPTY_FILTERS, MULTI } from "../useFilters";
import type { Facets, Filters } from "../types";

interface Chip {
  key: string;
  label: string;
  clear: Partial<Filters>;
}

const LABELS: Partial<Record<keyof Filters, string>> = {
  city: "City",
  company: "Company",
  department: "Dept",
  industry: "Industry",
  language: "Language",
  minScore: "Score ≥",
  maxYears: "Years ≤",
  postedAfter: "Since",
  status: "Status",
  remote: "Remote",
  hasConnection: "I know someone",
  hasDescription: "Has description",
  referral: "Referral",
  liked: "Liked",
  hidden: "Hidden",
  sent: "CV sent",
  reachedOut: "Reached out",
  profile: "CV",
  exclude: "Without",
};

/** Everything currently narrowing the results, as chips you can take off.
 *
 * With twenty-five controls down a scrolling sidebar, a filter set three
 * screens ago is invisible - and an unexplained "0 jobs match" is nearly
 * always one of those. This is the answer to "why am I seeing this?". */
export function ActiveFilters({
  filters,
  facets,
  update,
  reset,
}: {
  filters: Filters;
  facets?: Facets;
  update: (patch: Partial<Filters>) => void;
  reset: () => void;
}) {
  const companyNames = new Map((facets?.companies ?? []).map((c) => [c.id, c.name]));
  const chips: Chip[] = [];

  for (const [key, label] of Object.entries(LABELS) as [keyof Filters, string][]) {
    const value = filters[key];
    if (value === null || value === undefined || value === "") continue;
    if (value === EMPTY_FILTERS[key]) continue;

    if (MULTI.includes(key)) {
      // One chip per ticked value, so removing a city does not drop the rest.
      for (const item of String(value).split(",").filter(Boolean)) {
        const shown = key === "company" ? (companyNames.get(item) ?? item) : item;
        chips.push({
          key: `${key}:${item}`,
          label: `${label}: ${shown}`,
          clear: {
            [key]: String(value).split(",").filter((v) => v && v !== item).join(",") || null,
          } as Partial<Filters>,
        });
      }
      continue;
    }

    const shown = value === true ? "yes" : value === false ? "no" : String(value);
    chips.push({
      key,
      label: `${label}: ${shown}`,
      clear: { [key]: EMPTY_FILTERS[key] } as Partial<Filters>,
    });
  }

  if (chips.length === 0) return null;

  return (
    <div className="active-filters">
      {chips.map((chip) => (
        <button key={chip.key} type="button" className="chip" onClick={() => update(chip.clear)} title="Remove">
          {chip.label}
          <X size={11} weight="bold" aria-hidden="true" />
        </button>
      ))}
      {chips.length > 1 && (
        <button type="button" className="chip clear-all" onClick={reset}>
          clear all
        </button>
      )}
    </div>
  );
}
