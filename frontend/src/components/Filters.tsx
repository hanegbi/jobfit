import { useState } from "react";

import type { Facets, Filters as FilterState, SortKey } from "../types";
import { isInSet, toggleInSet } from "../useFilters";
import { SavedFilters } from "./SavedFilters";

interface Props {
  filters: FilterState;
  facets?: Facets;
  profiles: string[];
  update: (patch: Partial<FilterState>) => void;
  apply: (filters: FilterState) => void;
}

/** The real status values currently picked ("new", "seen", "closed", ...) -
 * empty for both "open only" and "everything", which are presets rather
 * than values of their own. */
function statusValues(status: string | null): string[] {
  if (!status || status === "open") return [];
  return status.split(",").filter(Boolean);
}

/** unset / yes / no. The old page could only say "on" and "off", which left
 * "show me jobs I have NOT hidden" unaskable. */
function Tri({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean | null;
  onChange: (next: boolean | null) => void;
}) {
  return (
    <div className="tri">
      <span>{label}</span>
      <div className="tri-buttons">
        {[
          { key: "any", v: null as boolean | null, text: "any" },
          { key: "yes", v: true as boolean | null, text: "yes" },
          { key: "no", v: false as boolean | null, text: "no" },
        ].map((option) => (
          <button
            key={option.key}
            type="button"
            aria-pressed={value === option.v}
            className={value === option.v ? "on" : ""}
            onClick={() => onChange(option.v)}
          >
            {option.text}
          </button>
        ))}
      </div>
    </div>
  );
}

/** A tickable list of values for one comma-separated filter. Collapsed to the
 * top few until asked, because there are 3,496 companies. */
function FacetGroup({
  title,
  options,
  total,
  selected,
  onToggle,
  searchable = false,
}: {
  title: string;
  options: { value: string; label: string; n: number }[];
  /** Distinct values that exist, which can exceed what the server sent. */
  total?: number;
  selected: string | null;
  onToggle: (value: string) => void;
  searchable?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState("");

  if (options.length === 0) return null;
  const matching = query
    ? options.filter((o) => o.label.toLowerCase().includes(query.toLowerCase()))
    : options;
  const shown = expanded ? matching.slice(0, 200) : matching.slice(0, 8);

  return (
    <div className="group">
      <h3>{title}</h3>
      {searchable && expanded && (
        <input
          className="facet-search"
          type="search"
          placeholder={`Filter ${title.toLowerCase()}…`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      )}
      <div className="facet-pills">
        {shown.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={isInSet(selected, option.value)}
            className={`facet${isInSet(selected, option.value) ? " on" : ""}`}
            onClick={() => onToggle(option.value)}
          >
            <span title={option.label}>{option.label}</span>
            <span className="count">{option.n.toLocaleString()}</span>
          </button>
        ))}
      </div>
      {matching.length > shown.length && !expanded && (
        <button type="button" className="link small" onClick={() => setExpanded(true)}>
          {/* "show all 250" would be a lie when the server capped 1,492 down
              to its 250 biggest. Say both numbers, or neither. */}
          {total != null && total > options.length
            ? `show ${matching.length} of ${total.toLocaleString()}`
            : `show all ${matching.length}`}
        </button>
      )}
      {expanded && (
        <button type="button" className="link small" onClick={() => setExpanded(false)}>
          show fewer
        </button>
      )}
      {total != null && total > options.length && (
        // Saying "show all 250" when 1,492 exist would be a lie; saying
        // nothing would make the missing ones look like they do not exist.
        <p className="facet-note">
          Showing the {options.length} biggest of {total.toLocaleString()}. Narrow the search to
          reach the rest.
        </p>
      )}
    </div>
  );
}

export function FiltersPanel({ filters, facets, profiles, update, apply }: Props) {
  return (
    <aside className="filters">
      {/* Search scope and excluded words are not filters - they modify the
          query, and live beside the search box where that query is typed. */}
      <SavedFilters filters={filters} apply={apply} />

      <label className="field">
        <span>Score against</span>
        <select value={filters.profile} onChange={(e) => update({ profile: e.target.value })}>
          <option value="best">Best of my CVs</option>
          {profiles.map((profile) => (
            <option key={profile} value={profile}>
              {profile}
            </option>
          ))}
        </select>
      </label>

      <label className="field">
        <span>Sort</span>
        <select value={filters.sort} onChange={(e) => update({ sort: e.target.value as SortKey })}>
          <option value="score">Best score</option>
          <option value="date">Newest</option>
          <option value="company">Company</option>
          <option value="title">Title</option>
        </select>
      </label>

      <div className="two-up">
        <label className="field">
          <span>Min score</span>
          <input
            type="number"
            min={0}
            max={100}
            value={filters.minScore ?? ""}
            placeholder="any"
            onChange={(e) => update({ minScore: e.target.value === "" ? null : Number(e.target.value) })}
          />
        </label>
        <label className="field">
          <span>Max years</span>
          <input
            type="number"
            min={0}
            max={20}
            value={filters.maxYears ?? ""}
            placeholder="any"
            onChange={(e) => update({ maxYears: e.target.value === "" ? null : Number(e.target.value) })}
          />
        </label>
      </div>

      <label className="field">
        <span>Posted since</span>
        <select
          value={filters.postedAfter ?? ""}
          onChange={(e) => update({ postedAfter: e.target.value || null })}
        >
          <option value="">Any time</option>
          {[
            { days: 7, label: "Last week" },
            { days: 30, label: "Last month" },
            { days: 90, label: "Last 3 months" },
          ].map(({ days, label }) => {
            const since = new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
            return (
              <option key={days} value={since}>
                {label}
              </option>
            );
          })}
        </select>
      </label>

      <label className="toggle-row">
        <input type="checkbox" checked={filters.group} onChange={(e) => update({ group: e.target.checked })} />
        <span>Group by company</span>
      </label>

      <Tri label="Remote" value={filters.remote} onChange={(remote) => update({ remote })} />
      <Tri label="I know someone" value={filters.hasConnection} onChange={(v) => update({ hasConnection: v })} />
      <Tri label="Has description" value={filters.hasDescription} onChange={(v) => update({ hasDescription: v })} />
      <Tri label="Referral" value={filters.referral} onChange={(referral) => update({ referral })} />
      <Tri label="Liked" value={filters.liked} onChange={(liked) => update({ liked })} />
      <Tri label="CV sent" value={filters.sent} onChange={(sent) => update({ sent })} />
      <Tri label="Reached out" value={filters.reachedOut} onChange={(v) => update({ reachedOut: v })} />
      <Tri label="Hidden" value={filters.hidden} onChange={(hidden) => update({ hidden })} />

      <div className="group">
        <h3>Status</h3>
        <div className="facet-pills">
          <button
            type="button"
            aria-pressed={filters.status === "open"}
            className={`facet${filters.status === "open" ? " on" : ""}`}
            onClick={() => update({ status: "open" })}
          >
            <span>open only</span>
          </button>
          <button
            type="button"
            aria-pressed={filters.status === null}
            className={`facet${filters.status === null ? " on" : ""}`}
            onClick={() => update({ status: null })}
          >
            <span>everything</span>
          </button>
          {Object.entries(facets?.statuses ?? {}).map(([status, count]) => {
            const selected = statusValues(filters.status).includes(status);
            return (
              <button
                key={status}
                type="button"
                aria-pressed={selected}
                className={`facet${selected ? " on" : ""}`}
                onClick={() => {
                  const current = statusValues(filters.status);
                  const next = selected ? current.filter((v) => v !== status) : [...current, status];
                  // An empty set falls back to "open only", not "everything" -
                  // unchecking your last specific status shouldn't suddenly
                  // bring closed jobs back.
                  update({ status: next.length ? next.join(",") : "open" });
                }}
              >
                <span>{status}</span>
                <span className="count">{count.toLocaleString()}</span>
              </button>
            );
          })}
        </div>
      </div>

      <FacetGroup
        title="City"
        total={facets?.totals.cities}
        options={(facets?.cities ?? []).map((c) => ({ value: c.city, label: c.city, n: c.n }))}
        selected={filters.city}
        onToggle={(value) => update({ city: toggleInSet(filters.city, value) })}
        searchable
      />
      <FacetGroup
        title="Company"
        total={facets?.totals.companies}
        options={(facets?.companies ?? []).map((c) => ({ value: c.id, label: c.name, n: c.n }))}
        selected={filters.company}
        onToggle={(value) => update({ company: toggleInSet(filters.company, value) })}
        searchable
      />
      <FacetGroup
        title="Department"
        total={facets?.totals.departments}
        options={(facets?.departments ?? []).map((d) => ({ value: d.department, label: d.department, n: d.n }))}
        selected={filters.department}
        onToggle={(value) => update({ department: toggleInSet(filters.department, value) })}
        searchable
      />
      <FacetGroup
        title="Industry"
        total={facets?.totals.industries}
        options={(facets?.industries ?? []).map((i) => ({ value: i.industry, label: i.industry, n: i.n }))}
        selected={filters.industry}
        onToggle={(value) => update({ industry: toggleInSet(filters.industry, value) })}
        searchable
      />
      <FacetGroup
        title="Language"
        total={facets?.totals.languages}
        options={(facets?.languages ?? []).map((l) => ({
          value: l.language,
          label: l.language === "he" ? "Hebrew" : l.language === "en" ? "English" : l.language,
          n: l.n,
        }))}
        selected={filters.language}
        onToggle={(value) => update({ language: toggleInSet(filters.language, value) })}
      />
    </aside>
  );
}
