/**
 * Conversation memory lives only in this browser tab.
 *
 * The API keeps no session state, so a follow-up is understood only when the
 * widget replays the recent turns. Nothing is written to storage and nothing
 * survives a reload.
 */

export type ChatRole = "user" | "assistant";

export type HistoryTurn = {
  role: ChatRole;
  text: string;
};

export type HistoryCandidate = HistoryTurn & {
  /** Static UI copy such as the welcome message. */
  excluded?: boolean;
  /** Still streaming, so it is not a complete turn yet. */
  pending?: boolean;
  /** Cancelled or errored, so replaying it would misrepresent the exchange. */
  failed?: boolean;
};

/** Kept in step with FLAMINGO_HISTORY_TURNS on the service. */
export const MAX_HISTORY_TURNS = 6;
/** Kept in step with FLAMINGO_HISTORY_CHARS on the service. */
export const MAX_HISTORY_CHARACTERS = 4000;
/** Kept in step with the per-turn limit of the request contract. */
export const MAX_TURN_CHARACTERS = 2000;

export function buildHistory(
  messages: readonly HistoryCandidate[],
  maxTurns: number = MAX_HISTORY_TURNS,
  maxCharacters: number = MAX_HISTORY_CHARACTERS,
): HistoryTurn[] {
  const usable: HistoryTurn[] = [];
  for (const message of messages) {
    if (message.excluded || message.pending || message.failed) continue;
    const text = message.text.trim().slice(0, MAX_TURN_CHARACTERS);
    if (text) usable.push({ role: message.role, text });
  }

  const turns = maxTurns > 0 ? usable.slice(-maxTurns) : [];
  let total = turns.reduce((sum, turn) => sum + turn.text.length, 0);
  while (turns.length > 1 && total > maxCharacters) {
    const dropped = turns.shift();
    total -= dropped ? dropped.text.length : 0;
  }
  return turns;
}
