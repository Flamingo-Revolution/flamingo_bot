import { describe, expect, it } from "vitest";

import { selectSuggestions, suggestionBank } from "./suggestions";

describe("suggested questions", () => {
  it("contains eleven distinct bilingual questions with compact labels", () => {
    expect(suggestionBank).toHaveLength(11);
    expect(new Set(suggestionBank.map((entry) => entry.id)).size).toBe(11);
    for (const entry of suggestionBank) {
      for (const locale of ["sq", "en"] as const) {
        expect(entry[locale].label.length).toBeLessThanOrEqual(32);
        expect(entry[locale].question.length).toBeGreaterThanOrEqual(entry[locale].label.length);
      }
      expect(entry.en.question).toMatch(/Answer in English\.$/);
    }
  });

  it("draws two distinct prompts without fetching or generating them", () => {
    const picked = selectSuggestions("sq", () => 0);
    expect(picked).toHaveLength(2);
    expect(picked.map((entry) => entry.id)).toEqual(["movement", "diaspora"]);
  });

  it("can draw the last entries, including the new protest question", () => {
    const picked = selectSuggestions("sq", () => 0.999);
    expect(picked.map((entry) => entry.id)).toContain("why_protesting");
    expect(picked[0].id).not.toBe(picked[1].id);
  });

  it("uses English on English pages and Albanian otherwise", () => {
    const english = selectSuggestions("en-US", () => 0);
    expect(english[0].label).toBe("Flamingo Revolution?");
    expect(english[0].question).toContain("Answer in English.");
    expect(selectSuggestions("sq-AL", () => 0)[0].label).toBe("Revolucioni Flamingo?");
    expect(selectSuggestions("", () => 0)[0].label).toBe("Revolucioni Flamingo?");
  });

  it("keeps the short protest label but sends the clarified question", () => {
    const entry = suggestionBank.find((suggestion) => suggestion.id === "why_protesting");
    expect(entry?.sq.label).toBe("Pse protestojnë shqiptarët?");
    expect(entry?.sq.question).toBe("Pse protestojnë shqiptarët me Revolucionin Flamingo?");
  });
});
