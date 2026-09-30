/** Placeholder cards in the shape of the real ones.
 *
 * A spinner says "wait"; these say "cards are coming, about this many, laid
 * out like this" - and because they hold the same space, the results do not
 * jump when they land. */
export function CardSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="list cards" aria-busy="true" aria-label="Loading jobs">
      {Array.from({ length: count }, (_, index) => (
        <div className="card skeleton" key={index}>
          <div className="card-head">
            <span className="sk sk-score" />
            <div className="card-heading">
              <span className="sk sk-title" style={{ width: `${55 + ((index * 13) % 35)}%` }} />
              <span className="sk sk-meta" />
            </div>
          </div>
          <span className="sk sk-line" />
          <span className="sk sk-line" style={{ width: "82%" }} />
        </div>
      ))}
    </div>
  );
}
