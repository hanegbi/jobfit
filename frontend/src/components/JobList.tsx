import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef } from "react";

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

/** Virtualized: 29,000 matches must cost the same to render as 50. */
export function JobList({
  jobs,
  selectedId,
  onSelect,
  compact = false,
}: {
  jobs: JobRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** With the detail panel open the list is narrow; the location column and
   * the per-row toggles go, because the panel shows both. */
  compact?: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: jobs.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 44,
    overscan: 12,
  });

  if (jobs.length === 0) {
    return <div className="list empty">No jobs match these filters.</div>;
  }

  return (
    <div className={`list${compact ? " compact" : ""}`} ref={scrollRef}>
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map((item) => (
          <div
            key={jobs[item.index].id}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              height: item.size,
              transform: `translateY(${item.start}px)`,
            }}
          >
            <Row
              job={jobs[item.index]}
              selected={jobs[item.index].id === selectedId}
              onSelect={onSelect}
              compact={compact}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
