import { describe, expect, it } from "vitest";

import { selectSuggestions, suggestionBank } from "./suggestions";

describe("suggested questions", () => {
  it("contains thirteen distinct bilingual questions with compact labels", () => {
    expect(suggestionBank).toHaveLength(13);
    expect(new Set(suggestionBank.map((entry) => entry.id)).size).toBe(13);
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

  it("can draw the last entry, including the police-response question", () => {
    const picked = selectSuggestions("sq", () => 0.999);
    expect(picked.map((entry) => entry.id)).toContain("police_response");
    expect(picked[0].id).not.toBe(picked[1].id);
  });

  it.each(["sq", "en"])("can draw both recent-event questions in %s", (locale) => {
    const draws = [0.85, 0.999];
    const picked = selectSuggestions(locale, () => draws.shift() ?? 0);
    expect(picked.map((entry) => entry.id)).toEqual(["protest_day_140", "police_response"]);
    expect(picked.map((entry) => entry.label)).toEqual(
      locale === "sq"
        ? ["Çfarë ndodhi më 17 tetor?", "Si reagoi policia?"]
        : ["What happened on 17 October?", "How did police respond?"],
    );
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

  it("preserves the tested event dates and day number in the submitted questions", () => {
    const day = suggestionBank.find((entry) => entry.id === "protest_day_140");
    expect(day?.sq.question).toBe("Çfarë ndodhi në ditën 140 të protestës, më 17 tetor 2026?");
    expect(day?.en.question).toBe(
      "What happened on protest day 140, 17 October 2026? Answer in English.",
    );

    const police = suggestionBank.find((entry) => entry.id === "police_response");
    expect(police?.sq.question).toBe(
      "Çfarë raportojnë burimet për veprimet e policisë gjatë protestave në Tiranë më 13–17 tetor 2026?",
    );
    expect(police?.en.question).toBe(
      "What do sources report about police actions during the Tirana protests on 13–17 October 2026? Answer in English.",
    );
  });
});
