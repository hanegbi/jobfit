import type { Facets, Filters as FilterState, SortKey } from "../types";

interface Props {
  filters: FilterState;
  facets?: Facets;
  total: number;
  update: (patch: Partial<FilterState>) => void;
  reset: () => void;
}

/** A tri-state control: unset / yes / no. The old page could only express
 * "on" and "off", which meant "show me jobs I have NOT hidden" was
 * unaskable. */
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

export function FiltersPanel({ filters, facets, total, update, reset }: Props) {
  const cities = facets?.cities?.slice(0, 14) ?? [];
  const companies = facets?.companies?.slice(0, 14) ?? [];
  const statuses = facets?.statuses ?? {};

  return (
    <aside className="filters">
      <div className="filters-head">
        <strong>{total.toLocaleString()}</strong> jobs
        <button type="button" className="link" onClick={reset}>
          clear all
        </button>
      </div>

      <label className="field">
        <span>Sort</span>
        <select value={filters.sort} onChange={(e) => update({ sort: e.target.value as SortKey })}>
          <option value="score">Best score</option>
          <option value="date">Newest</option>
          <option value="company">Company</option>
        </select>
      </label>

      <label className="field">
        <span>Minimum score</span>
        <input
          type="number"
          min={0}
          max={100}
          value={filters.minScore ?? ""}
          placeholder="any"
          onChange={(e) => update({ minScore: e.target.value === "" ? null : Number(e.target.value) })}
        />
      </label>

      <Tri label="Remote" value={filters.remote} onChange={(remote) => update({ remote })} />
      <Tri label="I know someone" value={filters.hasConnection} onChange={(v) => update({ hasConnection: v })} />
      <Tri label="Liked" value={filters.liked} onChange={(liked) => update({ liked })} />
      <Tri label="CV sent" value={filters.sent} onChange={(sent) => update({ sent })} />
      <Tri label="Hidden" value={filters.hidden} onChange={(hidden) => update({ hidden })} />

      <div className="group">
        <h3>Status</h3>
        <button
          type="button"
          className={`facet${filters.status === "open" ? " on" : ""}`}
          onClick={() => update({ status: "open" })}
        >
          <span>open only</span>
        </button>
        <button
          type="button"
          className={`facet${filters.status === null ? " on" : ""}`}
          onClick={() => update({ status: null })}
        >
          <span>everything</span>
        </button>
        {Object.entries(statuses).length === 0 && <p className="muted">–</p>}
        {Object.entries(statuses).map(([status, count]) => (
          <button
            key={status}
            type="button"
            className={`facet${filters.status === status ? " on" : ""}`}
            onClick={() => update({ status: filters.status === status ? null : status })}
          >
            <span>{status}</span>
            <span className="count">{count.toLocaleString()}</span>
          </button>
        ))}
      </div>

      <div className="group">
        <h3>City</h3>
        {cities.map(({ city, n }) => (
          <button
            key={city}
            type="button"
            className={`facet${filters.city === city ? " on" : ""}`}
            onClick={() => update({ city: filters.city === city ? null : city })}
          >
            <span>{city}</span>
            <span className="count">{n.toLocaleString()}</span>
          </button>
        ))}
      </div>

      <div className="group">
        <h3>Company</h3>
        {companies.map(({ id, name, n }) => (
          <button
            key={id}
            type="button"
            className={`facet${filters.company === id ? " on" : ""}`}
            onClick={() => update({ company: filters.company === id ? null : id })}
          >
            <span title={name}>{name}</span>
            <span className="count">{n.toLocaleString()}</span>
          </button>
        ))}
      </div>
    </aside>
  );
}
