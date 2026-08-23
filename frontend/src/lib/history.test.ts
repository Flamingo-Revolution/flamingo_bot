import { describe, expect, it } from "vitest";

import { buildHistory, MAX_TURN_CHARACTERS, type HistoryCandidate } from "./history";

const exchange: HistoryCandidate[] = [
  { role: "assistant", text: "Ask me about the Flamingo Revolution.", excluded: true },
  { role: "user", text: "  Çfarë është Revolucioni Flamingo?  " },
  { role: "assistant", text: "Një lëvizje qytetare [S1]." },
];

describe("buildHistory", () => {
  it("replays completed turns and drops static welcome copy", () => {
    expect(buildHistory(exchange)).toEqual([
      { role: "user", text: "Çfarë është Revolucioni Flamingo?" },
      { role: "assistant", text: "Një lëvizje qytetare [S1]." },
    ]);
  });

  it("omits pending, failed, and empty turns", () => {
    const messages: HistoryCandidate[] = [
      { role: "user", text: "Cancelled question", failed: true },
      { role: "assistant", text: "", pending: true },
      { role: "user", text: "   " },
      { role: "user", text: "Kept question" },
    ];

    expect(buildHistory(messages)).toEqual([{ role: "user", text: "Kept question" }]);
  });

  it("keeps only the most recent turns", () => {
    const messages: HistoryCandidate[] = Array.from({ length: 10 }, (_, index) => ({
      role: "user" as const,
      text: `question ${index}`,
    }));

    expect(buildHistory(messages, 3).map((turn) => turn.text)).toEqual([
      "question 7",
      "question 8",
      "question 9",
    ]);
  });

  it("clips a single oversized turn to the request contract", () => {
    const messages: HistoryCandidate[] = [{ role: "assistant", text: "x".repeat(3000) }];

    expect(buildHistory(messages)[0].text).toHaveLength(MAX_TURN_CHARACTERS);
  });

  it("drops the oldest turns to respect the character budget", () => {
    const messages: HistoryCandidate[] = [
      { role: "user", text: "a".repeat(80) },
      { role: "assistant", text: "b".repeat(80) },
      { role: "user", text: "c".repeat(80) },
    ];

    expect(buildHistory(messages, 6, 160).map((turn) => turn.text[0])).toEqual(["b", "c"]);
  });

  it("returns nothing when memory is disabled", () => {
    expect(buildHistory(exchange, 0)).toEqual([]);
  });
});
