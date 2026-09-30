import { beforeEach, describe, expect, it } from "vitest";

import { clearLegacyFlags, readLegacyFlags } from "./useLegacyFlags";

const store: Record<string, string> = {};
(globalThis as unknown as { localStorage: Storage }).localStorage = {
  getItem: (k: string) => store[k] ?? null,
  setItem: (k: string, v: string) => void (store[k] = v),
  removeItem: (k: string) => void delete store[k],
  clear: () => Object.keys(store).forEach((k) => delete store[k]),
  key: () => null,
  length: 0,
} as Storage;

describe("rescuing the old page's flags", () => {
  beforeEach(() => Object.keys(store).forEach((k) => delete store[k]));

  it("finds nothing when the browser holds nothing", () => {
    expect(readLegacyFlags()).toBe(null);
  });

  it("collects every non-empty key and counts the flags", () => {
    store.jobfit_liked = JSON.stringify(["a", "b"]);
    store.jobfit_hidden = JSON.stringify(["c"]);
    store.jobfit_sent = JSON.stringify([]);
    const found = readLegacyFlags();
    expect(found?.count).toBe(3);
    expect(found?.payload).toEqual({ jobfit_liked: ["a", "b"], jobfit_hidden: ["c"] });
  });

  it("ignores corrupt values rather than throwing", () => {
    store.jobfit_liked = "not json";
    store.jobfit_hidden = JSON.stringify(["c"]);
    expect(readLegacyFlags()?.count).toBe(1);
  });

  it("clears every key it reads", () => {
    store.jobfit_liked = JSON.stringify(["a"]);
    clearLegacyFlags();
    expect(readLegacyFlags()).toBe(null);
  });
});
