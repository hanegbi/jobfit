import { useCallback, useEffect, useState } from "react";

/** The keys the old static page wrote. They are readable here only if that
 * page was served from this origin (http://127.0.0.1:8787/jobfit.html); a
 * page opened off disk is a different origin and needs the import script. */
const KEYS = ["jobfit_liked", "jobfit_hidden", "jobfit_sent", "jobfit_reached"] as const;

export interface LegacyFlags {
  payload: Record<string, string[]>;
  count: number;
}

/** Whatever the old page left in this browser, or null when there is none. */
export function readLegacyFlags(): LegacyFlags | null {
  const payload: Record<string, string[]> = {};
  let count = 0;
  for (const key of KEYS) {
    try {
      const parsed = JSON.parse(localStorage.getItem(key) || "null");
      if (Array.isArray(parsed) && parsed.length > 0) {
        payload[key] = parsed as string[];
        count += parsed.length;
      }
    } catch {
      // Private mode, blocked storage, or something that is not JSON: there
      // is simply nothing to rescue, which is not an error worth showing.
    }
  }
  return count > 0 ? { payload, count } : null;
}

export function clearLegacyFlags(): void {
  for (const key of KEYS) {
    try {
      localStorage.removeItem(key);
    } catch {
      // Nothing to do: the offer just reappears next time.
    }
  }
}

/** Offers a one-time rescue of the old page's flags into the database. */
export function useLegacyFlags() {
  const [found, setFound] = useState<LegacyFlags | null>(null);
  const [imported, setImported] = useState<number | null>(null);

  useEffect(() => setFound(readLegacyFlags()), []);

  const importThem = useCallback(async () => {
    if (!found) return;
    const res = await fetch("/api/state/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(found.payload),
    });
    if (!res.ok) return;
    const result = (await res.json()) as { applied: Record<string, number> };
    // Only clear once the server has them: a failed import that wiped the
    // browser's copy would lose the flags for good.
    clearLegacyFlags();
    setImported(Object.values(result.applied).reduce((a, b) => a + b, 0));
    setFound(null);
  }, [found]);

  const dismiss = useCallback(() => setFound(null), []);

  return { found, imported, importThem, dismiss };
}
