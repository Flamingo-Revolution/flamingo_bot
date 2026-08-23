/**
 * Turning a refused request into something a visitor can act on.
 *
 * The service refuses for reasons that call for different responses. A burst
 * limit clears in seconds, an hourly allowance clears within the hour, and the
 * daily budget does not clear until tomorrow. Telling a visitor to "wait before
 * trying again" when the answer is "come back tomorrow" invites them to sit and
 * retry against a wall, so each reason gets its own wording.
 *
 * Albanian leads, as it does in the welcome message, because most visitors read
 * it first. A refusal is a poor moment to make someone read a second language to
 * find out what happened. The Albanian side is written in Diella's voice and is
 * deliberately light: the limits exist to keep a volunteer project affordable,
 * not to tell anyone off for asking too much.
 */

/** Mirrors the ``detail`` values the service sends with a refusal. */
export type RefusalReason =
  | "client_quota_exhausted"
  | "daily_quota_exhausted"
  | "too_many_requests"
  | "unavailable"
  | "request_failed";

type Bilingual = {
  sq: string;
  en: string;
};

/**
 * What happened, shown in the conversation where the visitor is reading.
 *
 * This must never contradict the remedy below it. A bubble that says "try again
 * in a moment" above an alert that says "come back tomorrow" leaves the visitor
 * retrying against a limit that will not move for hours.
 */
const BUBBLES: Record<RefusalReason, Bilingual> = {
  client_quota_exhausted: {
    sq: "Diellën e zuri pak vapa nga aq shumë pyetje për këtë orë.",
    en: "Diella is a little overheated from this hour's questions.",
  },
  daily_quota_exhausted: {
    sq: "Diella i mbaroi përgjigjet për sot dhe shkoi të pushojë.",
    en: "Diella is out of answers for today and has gone to rest.",
  },
  too_many_requests: {
    sq: "Ngadalë pak, se Diella nuk i arrin dot të gjitha njëherësh!",
    en: "Slow down a little, Diella cannot keep up with them all at once.",
  },
  unavailable: {
    sq: "Diella nuk po përgjigjet për momentin.",
    en: "Diella is not responding at the moment.",
  },
  request_failed: {
    sq: "Diella nuk arriti ta përfundonte këtë kërkesë.",
    en: "Diella could not complete that request.",
  },
};

/** What to do about it, shown as an alert under the input. */
const MESSAGES: Record<RefusalReason, Bilingual> = {
  client_quota_exhausted: {
    sq: "Të lutem eja pak më vonë.",
    en: "Please come back a little later.",
  },
  daily_quota_exhausted: {
    sq: "Të lutem kthehu nesër.",
    en: "Please come back tomorrow.",
  },
  too_many_requests: {
    sq: "Prit një çast dhe provo sërish.",
    en: "Please wait a moment and try again.",
  },
  unavailable: {
    sq: "Provo sërish pas pak.",
    en: "Please try again shortly.",
  },
  request_failed: {
    sq: "Provo sërish pas pak.",
    en: "Please try again shortly.",
  },
};

function resolve(source: Record<RefusalReason, Bilingual>, reason: string): Bilingual {
  return reason in source ? source[reason as RefusalReason] : source.request_failed;
}

/** Names the refusal without trusting the body to be JSON, or to be present at all. */
export async function refusalReason(response: {
  status: number;
  json: () => Promise<unknown>;
}): Promise<RefusalReason> {
  if (response.status === 503) return "unavailable";
  if (response.status !== 429) return "request_failed";
  try {
    const body = (await response.json()) as { detail?: unknown };
    const detail = typeof body?.detail === "string" ? body.detail : "";
    if (detail === "client_quota_exhausted" || detail === "daily_quota_exhausted") {
      return detail;
    }
  } catch {
    // An empty or non-JSON body still means the request was refused as too many.
  }
  return "too_many_requests";
}

/** A blank line, so the widget's rich text renders the two languages as paragraphs. */
export function refusalBubble(reason: string): string {
  const copy = resolve(BUBBLES, reason);
  return `${copy.sq}\n\n${copy.en}`;
}

/** A single break, because the alert is small type and does not want a blank line. */
export function refusalMessage(reason: string): string {
  const copy = resolve(MESSAGES, reason);
  return `${copy.sq}\n${copy.en}`;
}
