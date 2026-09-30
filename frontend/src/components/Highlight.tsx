import { useMemo } from "react";

// The same characters search.py's tokeniser keeps: word characters plus the
// ones that carry meaning here - C++, C#, .NET, node.js, Hebrew.
const TERM_RE = /[0-9A-Za-z֐-׿#+.]+/g;

function escapeRegExp(term: string): string {
  return term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** The searched-for terms marked up inside a piece of text.
 *
 * Built from the same term split the server uses, so what lights up is what
 * actually matched rather than a naive substring of the raw query. */
export function Highlight({ text, query }: { text: string; query: string }) {
  const pattern = useMemo(() => {
    const terms = (query.match(TERM_RE) ?? []).filter((term) => term.length > 1);
    if (terms.length === 0) return null;
    // Longest first, so "kubernetes" wins over "k8" when both are present.
    terms.sort((a, b) => b.length - a.length);
    return new RegExp(`(${terms.map(escapeRegExp).join("|")})`, "gi");
  }, [query]);

  if (!pattern) return <>{text}</>;
  const parts = text.split(pattern);
  return (
    <>
      {parts.map((part, index) =>
        // split() with one capture group puts the matches at the odd indexes.
        index % 2 === 1 ? <mark key={index}>{part}</mark> : part,
      )}
    </>
  );
}
