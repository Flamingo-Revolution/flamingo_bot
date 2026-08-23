// Participation index for the test protests.
//
// The headline series is `peak`, a top-10 peak frame average from a crowd-counting
// model over the livestream frames.
// Day 2 is anchored to an on-the-ground geometry estimate.

export type ParticipationDay = {
  day: number;
  /** ISO date of the protest day. */
  date: string;
  saturday: boolean;
  peak: number | null;
  mean: number | null;
  median: number | null;
  source: string;
  note: { sq: string; en: string };
  noteLink?: { href: string; word: { sq: string; en: string } };
};

const yt = (id: string) => `https://www.youtube.com/watch?v=${id}`;

export const participation: ParticipationDay[] = [
  { day: 1, date: "2026-05-31", saturday: false, peak: 4.78, mean: 3.89, median: 4.22, source: yt("firstStream"),
    note: { sq: "Dita e parë e protestës.", en: "The first day of protest." } },
  { day: 2, date: "2026-06-06", saturday: true, peak: 100.0, mean: 19.43, median: 15.76, source: yt("peakStream"),
    note: { sq: "Dita më e madhe: mbi 100 mijë në shesh.", en: "The biggest day: over 100,000 in the square." },
    noteLink: { href: "https://example.org/day-two", word: { sq: "shesh", en: "square" } } },
  { day: 3, date: "2026-06-07", saturday: false, peak: null, mean: null, median: null, source: yt("missingStream"),
    note: { sq: "Pa analizë të transmetimit.", en: "No broadcast analysis." } },
];

export type ParticipationEvent = {
  day: number;
  tier: "peak" | "primary" | "secondary";
  label: { sq: string; en: string };
  sub: { sq: string; en: string };
};

export const participationEvents: ParticipationEvent[] = [
  { day: 2, tier: "peak",
    label: { sq: "Kulmi", en: "The peak" },
    sub: { sq: "6 qershor", en: "6 June" } },
];

/** Normalization reference shown in the methodology note. */
export const NORMALIZATION = { index100Day: 2, index100Date: "2026-06-06" };
