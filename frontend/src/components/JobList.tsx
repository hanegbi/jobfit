import { useVirtualizer } from "@tanstack/react-virtual";
import { useMemo, useRef } from "react";

import type { JobRow } from "../types";
import { useToggleJobState, type StateFlag } from "../useJobState";

const FLAGS: { flag: StateFlag; label: string; title: string }[] = [
  { flag: "liked", label: "♥", title: "Liked" },
  { flag: "sent", label: "➤", title: "CV sent" },
  { flag: "reached_out", label: "✆", title: "Reached out" },
  { flag: "hidden", label: "✕", title: "Hidden" },
];

function scoreClass(score: number | null): string {
  if (score === null) return "score none";
  if (score >= 70) return "score strong";
  if (score >= 50) return "score fair";
  return "score weak";
}

function Row({
  job,
  selected,
  onSelect,
  compact,
}: {
  job: JobRow;
  selected: boolean;
  onSelect: (id: string) => void;
  compact: boolean;
}) {
  const toggle = useToggleJobState();
  const state = { liked: job.liked, hidden: job.hidden, sent: job.sent, reached_out: job.reached_out };

  return (
    <div
      className={`row${selected ? " selected" : ""}${job.hidden ? " is-hidden" : ""}`}
      onClick={() => onSelect(job.id)}
    >
      <span className={scoreClass(job.best_score)}>{job.best_score ?? "–"}</span>
      <span className="title" title={job.title}>
        {job.title}
        {job.status === "new" && <span className="tag new">new</span>}
        {job.status === "closed" && <span className="tag closed">closed</span>}
        {job.is_referral && <span className="tag referral">referral</span>}
        {job.source_language === "he" && (
          <span className="tag translated" title="Machine-translated from Hebrew">
            translated
          </span>
        )}
      </span>
      <span className="company" title={job.company}>
        {job.company}
        {job.connection_count > 0 && (
          <span className="tag connection" title={`${job.connection_count} contact(s) here`}>
            {job.connection_count} known
          </span>
        )}
      </span>
      {!compact && <span className="where">{job.is_remote ? "Remote" : (job.city ?? job.location ?? "–")}</span>}
      <span className="flags">
        {(compact ? FLAGS.slice(0, 1) : FLAGS).map(({ flag, label, title }) => (
          <button
            key={flag}
            type="button"
            title={title}
            className={`flag${state[flag] ? " on" : ""}`}
            onClick={(event) => {
              event.stopPropagation();
              toggle.mutate({ jobId: job.id, flag, current: state });
            }}
          >
            {label}
          </button>
        ))}
      </span>
    </div>
  );
}

type Item = { kind: "header"; key: string; company: string; n: number } | { kind: "job"; key: string; job: JobRow };

/** Company headings interleaved into the row list, in the order the sort
 * already put the companies in - so grouping by company while sorted by score
 * still leads with the company holding the best job, as the old page did. */
export function toItems(jobs: JobRow[], group: boolean): Item[] {
  if (!group) return jobs.map((job) => ({ kind: "job", key: job.id, job }));
  const order: string[] = [];
  const byCompany = new Map<string, JobRow[]>();
  for (const job of jobs) {
    const existing = byCompany.get(job.company_id);
    if (existing) existing.push(job);
    else {
      order.push(job.company_id);
      byCompany.set(job.company_id, [job]);
    }
  }
  const items: Item[] = [];
  for (const companyId of order) {
    const rows = byCompany.get(companyId)!;
    items.push({ kind: "header", key: `h:${companyId}`, company: rows[0].company, n: rows.length });
    for (const job of rows) items.push({ kind: "job", key: job.id, job });
  }
  return items;
}

/** Virtualized: 29,000 matches must cost the same to render as 50. */
export function JobList({
  jobs,
  selectedId,
  onSelect,
  compact = false,
  group = false,
}: {
  jobs: JobRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** With the detail panel open the list is narrow; the location column and
   * the per-row toggles go, because the panel shows both. */
  compact?: boolean;
  group?: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const items = useMemo(() => toItems(jobs, group), [jobs, group]);
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => (items[index].kind === "header" ? 30 : 44),
    overscan: 12,
  });

  if (jobs.length === 0) {
    return <div className="list empty">No jobs match these filters.</div>;
  }

  return (
    <div className={`list${compact ? " compact" : ""}`} ref={scrollRef}>
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map((virtual) => {
          const item = items[virtual.index];
          return (
            <div
              key={item.key}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                right: 0,
                height: virtual.size,
                transform: `translateY(${virtual.start}px)`,
              }}
            >
              {item.kind === "header" ? (
                <div className="group-head">
                  <span>{item.company}</span>
                  <span className="count">{item.n}</span>
                </div>
              ) : (
                <Row
                  job={item.job}
                  selected={item.job.id === selectedId}
                  onSelect={onSelect}
                  compact={compact}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
