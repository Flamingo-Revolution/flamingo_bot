import { describe, expect, it } from "vitest";

import { refusalBubble, refusalMessage, refusalReason } from "./refusal";

const REASONS = [
  "client_quota_exhausted",
  "daily_quota_exhausted",
  "too_many_requests",
  "unavailable",
  "request_failed",
];

const QUOTA_REASONS = ["client_quota_exhausted", "daily_quota_exhausted"];

function response(status: number, body: unknown): Parameters<typeof refusalReason>[0] {
  return {
    status,
    json: async () => {
      if (body === undefined) throw new SyntaxError("Unexpected end of JSON input");
      return body;
    },
  };
}

describe("refusalReason", () => {
  it("names each quota separately so the wording can differ", async () => {
    expect(await refusalReason(response(429, { detail: "client_quota_exhausted" }))).toBe(
      "client_quota_exhausted",
    );
    expect(await refusalReason(response(429, { detail: "daily_quota_exhausted" }))).toBe(
      "daily_quota_exhausted",
    );
  });

  it("treats the burst limiter as a plain too-many-requests", async () => {
    expect(await refusalReason(response(429, { detail: "rate_limit_exceeded" }))).toBe(
      "too_many_requests",
    );
  });

  it("falls back when a refusal carries no readable body", async () => {
    expect(await refusalReason(response(429, undefined))).toBe("too_many_requests");
    expect(await refusalReason(response(429, "not an object"))).toBe("too_many_requests");
  });

  it("separates an unreadable quota store from a refused request", async () => {
    expect(await refusalReason(response(503, { detail: "quota_unavailable" }))).toBe("unavailable");
    expect(await refusalReason(response(500, {}))).toBe("request_failed");
  });
});

describe("bilingual copy", () => {
  it("leads in Albanian and follows in English, for every reason", () => {
    for (const reason of REASONS) {
      const [albanian, english] = refusalBubble(reason).split("\n\n");
      // Albanian declines the name, so match the stem: Diella, Diellën, Diellës.
      expect(albanian).toMatch(/Diell(a|ën|ës)\b/);
      expect(english).toContain("Diella");
      // The Albanian line carries diacritics no English line would.
      expect(/[ëç]/i.test(albanian)).toBe(true);
      expect(/[ëç]/i.test(english)).toBe(false);
    }
  });

  it("gives the alert two lines rather than a blank line between them", () => {
    for (const reason of REASONS) {
      expect(refusalMessage(reason).split("\n")).toHaveLength(2);
    }
  });

  it("never leaves a language empty", () => {
    for (const reason of REASONS) {
      const parts = [
        ...refusalBubble(reason).split("\n\n"),
        ...refusalMessage(reason).split("\n"),
      ];
      for (const part of parts) {
        expect(part.trim().length).toBeGreaterThan(0);
      }
    }
  });
});

describe("refusalMessage", () => {
  it("tells a visitor out for the day to come back tomorrow, not to retry", () => {
    expect(refusalMessage("daily_quota_exhausted")).toContain("tomorrow");
    expect(refusalMessage("daily_quota_exhausted")).toContain("nesër");
    expect(refusalMessage("client_quota_exhausted")).toContain("later");
    expect(refusalMessage("client_quota_exhausted")).toContain("më vonë");
    expect(refusalMessage("too_many_requests")).toContain("wait");
  });

  it("falls back for an unrecognised reason", () => {
    expect(refusalMessage("something_new")).toBe(refusalMessage("request_failed"));
  });
});

describe("refusalBubble", () => {
  it("never invites an immediate retry the alert then contradicts", () => {
    for (const reason of QUOTA_REASONS) {
      const shown = `${refusalBubble(reason)} ${refusalMessage(reason)}`;
      expect(shown).not.toContain("in a moment");
      expect(shown).not.toContain("try again");
      expect(shown).not.toContain("sërish");
    }
  });

  it("names the limit the visitor actually hit", () => {
    expect(refusalBubble("client_quota_exhausted")).toContain("hour");
    expect(refusalBubble("client_quota_exhausted")).toContain("orë");
    expect(refusalBubble("daily_quota_exhausted")).toContain("today");
    expect(refusalBubble("daily_quota_exhausted")).toContain("sot");
  });

  it("falls back for an unrecognised reason", () => {
    expect(refusalBubble("something_new")).toBe(refusalBubble("request_failed"));
  });
});
