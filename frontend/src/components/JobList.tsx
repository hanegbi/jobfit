import { ArrowUpRight, Heart, PaperPlaneTilt, Phone, EyeSlash } from "@phosphor-icons/react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useMemo, useRef } from "react";

import type { Contact, JobRow } from "../types";
import { useToggleJobState, type StateFlag } from "../useJobState";
import { Highlight } from "./Highlight";

const ICON = { size: 15, weight: "bold" } as const;

const FLAGS: { flag: StateFlag; label: string; title: string; Icon: typeof Heart }[] = [
  { flag: "liked", label: "Like", title: "Liked", Icon: Heart },
  { flag: "sent", label: "Sent", title: "CV sent", Icon: PaperPlaneTilt },
  { flag: "reached_out", label: "Reached out", title: "Reached out", Icon: Phone },
  { flag: "hidden", label: "Hide", title: "Hidden", Icon: EyeSlash },
];

/** Names beyond this go behind a "+N more" whose tooltip lists them. Three
 * fits one line at the narrowest card width; a company where 64 contacts work
 * must not push the description off the card. */
const CONTACTS_SHOWN = 3;

function scoreClass(score: number | null): string {
  if (score === null) return "score none";
  if (score >= 70) return "score strong";
  if (score >= 50) return "score fair";
  return "score weak";
}

function describe(contact: Contact): string {
  return contact.position ? `${contact.name}, ${contact.position}` : contact.name;
}

function Contacts({ contacts, count }: { contacts: Contact[]; count: number }) {
  if (count === 0) return null;
  // The count is stored with the names, so they agree - but a company added
  // since the last connections upload can still have a count and no names.
  if (contacts.length === 0) {
    return <span className="contacts">{count} contact{count === 1 ? "" : "s"} here</span>;
  }
  const shown = contacts.slice(0, CONTACTS_SHOWN);
  const rest = contacts.slice(CONTACTS_SHOWN);
  return (
    <span className="contacts">
      <span className="who">You know</span>
      {shown.map((contact) =>
        contact.url ? (
          <a
            key={contact.name}
            className="contact"
            href={contact.url}
            target="_blank"
            rel="noopener noreferrer"
            title={describe(contact)}
            onClick={(event) => event.stopPropagation()}
          >
            {contact.name}
          </a>
        ) : (
          <span key={contact.name} className="contact" title={describe(contact)}>
            {contact.name}
          </span>
        ),
      )}
      {rest.length > 0 && (
        // A real popover rather than a title attribute: 34 names in a native
        // tooltip is an unreadable wall that takes a second to appear and
        // cannot be clicked through to anyone's profile.
        // A <button>, not role="tooltip": ARIA forbids interactive content
        // (these are real links out to LinkedIn) inside a tooltip, and
        // screen readers won't let a user navigate into one. This is a
        // disclosure panel that happens to open on hover as well as focus.
        <button type="button" className="contact more" aria-haspopup="true">
          +{rest.length} more
          <span className="contact-popover">
            {rest.map((contact) => (
              <a
                key={contact.name}
                href={contact.url ?? undefined}
                target="_blank"
                rel="noopener noreferrer"
              >
                <strong>{contact.name}</strong>
                {contact.position && <em>{contact.position}</em>}
              </a>
            ))}
          </span>
        </button>
      )}
    </span>
  );
}

function Card({ job, query }: { job: JobRow; query: string }) {
  const toggle = useToggleJobState();
  const state = { liked: job.liked, hidden: job.hidden, sent: job.sent, reached_out: job.reached_out };
  const where = job.is_remote ? "Remote" : (job.city ?? job.location ?? null);

  return (
    <article className={`card${job.hidden ? " is-hidden" : ""}${job.liked ? " is-liked" : ""}`}>
      <div className="card-head">
        <span className={scoreClass(job.best_score)}>{job.best_score ?? "--"}</span>
        <div className="card-heading">
          {/* The title is the link out. There is no in-app detail view: the
              posting itself is the thing you actually want to read. */}
          {job.url ? (
            <a className="card-title" href={job.url} target="_blank" rel="noopener noreferrer">
              <Highlight text={job.title} query={query} />
              <ArrowUpRight size={13} weight="bold" className="out" aria-label="opens the posting" />
            </a>
          ) : (
            <span className="card-title">
              <Highlight text={job.title} query={query} />
            </span>
          )}
          <p className="card-meta">
            <span className="company">{job.company}</span>
            {where && <span>{where}</span>}
            {job.department && <span>{job.department}</span>}
            {job.employment_type && <span>{job.employment_type}</span>}
            {job.years_required != null && <span>{job.years_required}+ yrs</span>}
            {job.status === "new" && <span className="tag new">new</span>}
            {job.status === "closed" && <span className="tag closed">closed</span>}
            {job.is_referral && <span className="tag referral">referral</span>}
            {job.source_language === "he" && (
              <span className="tag translated" title="Machine-translated from Hebrew">
                translated
              </span>
            )}
          </p>
        </div>
        {/* Top right, level with the title: these are the actions, and an
            action you have to read the whole card to reach is one you take
            less often. */}
        <span className="card-flags">
          {FLAGS.map(({ flag, label, title, Icon }) => (
            <button
              key={flag}
              type="button"
              title={title}
              // The visible label collapses to icon-only at narrow widths
              // (.flag span { display: none }) - title alone isn't reliable
              // for screen readers or touch, so the accessible name doesn't
              // depend on the label staying visible.
              aria-label={label}
              aria-pressed={state[flag]}
              className={`flag${state[flag] ? " on" : ""}`}
              onClick={() => toggle.mutate({ jobId: job.id, flag, current: state })}
            >
              <Icon {...ICON} weight={state[flag] ? "fill" : "bold"} />
              <span>{label}</span>
            </button>
          ))}
        </span>
      </div>

      {job.snippet && (
        <p className="card-snippet">
          <Highlight text={job.snippet.trim()} query={query} />
        </p>
      )}

      <div className="card-foot">
        <Contacts contacts={job.contacts} count={job.connection_count} />
      </div>
    </article>
  );
}

type Item =
  | { kind: "header"; key: string; company: string; n: number }
  | { kind: "job"; key: string; job: JobRow };

/** Company headings interleaved into the card list, in the order the sort
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

const CARD_HEIGHT = 150;
const HEADER_HEIGHT = 34;

/** Virtualized: 29,000 matches must cost the same to render as 50. Cards are
 * measured after mount, because a card with no description is shorter than one
 * with three lines of it. */
export function JobList({
  jobs,
  group = false,
  query = "",
  onClearFilters,
}: {
  jobs: JobRow[];
  group?: boolean;
  query?: string;
  onClearFilters?: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const items = useMemo(() => toItems(jobs, group), [jobs, group]);
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => (items[index].kind === "header" ? HEADER_HEIGHT : CARD_HEIGHT),
    overscan: 6,
  });

  if (jobs.length === 0) {
    // Composed, not a shrug: an empty result is nearly always one filter too
    // many, so the way out is on screen next to the bad news.
    return (
      <div className="list empty">
        <p className="empty-title">Nothing matches.</p>
        <p className="empty-body">
          {query
            ? <>No job mentions <strong>{query}</strong> with the filters you have on.</>
            : <>The filters you have on rule out every job.</>}
        </p>
        {onClearFilters && (
          <button type="button" className="empty-action" onClick={onClearFilters}>
            Clear all filters
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="list cards" ref={scrollRef}>
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map((virtual) => {
          const item = items[virtual.index];
          return (
            <div
              key={item.key}
              className="virtual-item"
              ref={virtualizer.measureElement}
              data-index={virtual.index}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                right: 0,
                transform: `translateY(${virtual.start}px)`,
              }}
            >
              {item.kind === "header" ? (
                <div className="group-head">
                  <span>{item.company}</span>
                  <span className="count">{item.n}</span>
                </div>
              ) : (
                <Card job={item.job} query={query} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
