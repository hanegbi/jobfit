import { describe, expect, it } from "vitest";

import { nextState } from "./useJobState";

const NONE = { liked: false, hidden: false, sent: false, reached_out: false };

describe("toggling a job's state", () => {
  it("flips only the named flag", () => {
    expect(nextState({ ...NONE, sent: true }, "liked")).toEqual({ ...NONE, sent: true, liked: true });
  });

  it("flips back off", () => {
    expect(nextState({ ...NONE, liked: true }, "liked").liked).toBe(false);
  });

  it("leaves the original untouched", () => {
    const current = { ...NONE };
    nextState(current, "hidden");
    expect(current.hidden).toBe(false);
  });
});
