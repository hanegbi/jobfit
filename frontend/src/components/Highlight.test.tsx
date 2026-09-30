import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Highlight } from "./Highlight";

const render = (text: string, query: string) =>
  renderToStaticMarkup(<Highlight text={text} query={query} />);

describe("highlighting what matched", () => {
  it("marks every term, case-insensitively", () => {
    const html = render("Senior Platform Engineer", "platform engineer");
    expect(html).toContain("<mark>Platform</mark>");
    expect(html).toContain("<mark>Engineer</mark>");
  });

  it("marks inside a word, the way the search matched it", () => {
    expect(render("security engineering", "engineer")).toContain("<mark>engineer</mark>ing");
  });

  it("leaves the text alone when there is no query", () => {
    expect(render("Senior Backend Engineer", "")).toBe("Senior Backend Engineer");
  });

  it("treats regex characters in the query as text, not syntax", () => {
    // A naive implementation throws or mis-highlights on these.
    for (const query of ["C++", "(", "a|b", "*", ".NET", "["]) {
      expect(() => render("Senior C++ and .NET developer", query)).not.toThrow();
    }
    expect(render("Senior C++ developer", "C++")).toContain("<mark>C++</mark>");
  });

  it("ignores one-character terms, which would mark half the page", () => {
    expect(render("Senior Backend Engineer", "a")).toBe("Senior Backend Engineer");
  });

  it("prefers the longest term where two overlap", () => {
    expect(render("kubernetes administrator", "kube kubernetes")).toContain("<mark>kubernetes</mark>");
  });

  it("marks a Hebrew term", () => {
    expect(render("מהנדס תוכנה", "מהנדס")).toContain("<mark>מהנדס</mark>");
  });
});
