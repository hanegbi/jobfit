import { useQuery } from "@tanstack/react-query";

import { fetchJob } from "../api";
import { useToggleJobState, type StateFlag } from "../useJobState";

const FLAGS: { flag: StateFlag; label: string }[] = [
  { flag: "liked", label: "♥ Liked" },
  { flag: "sent", label: "➤ CV sent" },
  { flag: "reached_out", label: "✆ Reached out" },
  { flag: "hidden", label: "✕ Hidden" },
];

/** Fetched on demand: the description is the reason list rows don't carry one. */
export function JobDetail({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const toggle = useToggleJobState();
  const { data: job, isLoading, error } = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => fetchJob(jobId),
  });

  if (isLoading) return <section className="detail"><p className="muted">Loading…</p></section>;
  if (error || !job) {
    return (
      <section className="detail">
        <p className="muted">Could not load this job.</p>
        <button type="button" onClick={onClose}>close</button>
      </section>
    );
  }

  return (
    <section className="detail">
      <header>
        <div>
          <h2>{job.title}</h2>
          <p className="sub">
            {job.company}
            {job.city && ` · ${job.city}`}
            {job.is_remote && " · Remote"}
            {job.employment_type && ` · ${job.employment_type}`}
            {job.status === "closed" && <span className="tag closed">closed</span>}
          </p>
        </div>
        <button type="button" className="close" onClick={onClose} title="Close">
          ✕
        </button>
      </header>

      <div className="scores">
        {Object.entries(job.scores).map(([profile, score]) => (
          <div key={profile} className="score-card">
            <strong>{score.score ?? "–"}</strong>
            <span>{profile}</span>
            {score.confidence === "title_only" && <em title="No description to check requirements against">title only</em>}
            {score.coverage != null && <em>{Math.round(score.coverage)}% of its requirements</em>}
          </div>
        ))}
      </div>

      {Object.values(job.scores).some((s) => s.matched.length > 0) && (
        <p className="matched">
          Matched:{" "}
          {[...new Set(Object.values(job.scores).flatMap((s) => s.matched))].slice(0, 24).join(", ")}
        </p>
      )}

      <div className="actions">
        {FLAGS.map(({ flag, label }) => (
          <button
            key={flag}
            type="button"
            className={job.state[flag] ? "on" : ""}
            onClick={() => toggle.mutate({ jobId: job.id, flag, current: job.state })}
          >
            {label}
          </button>
        ))}
        {job.url && (
          <a className="apply" href={job.url} target="_blank" rel="noreferrer">
            Open posting ↗
          </a>
        )}
      </div>

      {job.connection_count > 0 && (
        <p className="connection-note">{job.connection_count} of your contacts work at {job.company}.</p>
      )}

      <pre className="description">{job.description || "No description was scraped for this job."}</pre>
    </section>
  );
}
