/**
 * The answer model writes light Markdown, so the widget renders a deliberately
 * small subset of it: paragraphs, bullet and numbered lists, bold, and italic.
 *
 * Parsing produces a node tree that the component renders with real elements.
 * Nothing here reaches `{@html}`, so neither the model nor a quoted evidence
 * block can introduce markup, a link, or a script into the host page. Anything
 * outside the subset stays visible as the literal characters the model wrote.
 */

export type InlineNode = {
  kind: "text" | "strong" | "em";
  text: string;
};

export type RichBlock =
  | { kind: "paragraph"; inlines: InlineNode[] }
  | { kind: "list"; ordered: boolean; items: InlineNode[][] };

const BULLET_ITEM = /^ {0,3}[-*•]\s+(.*)$/;
const NUMBERED_ITEM = /^ {0,3}\d{1,2}[.)]\s+(.*)$/;
const WORD_CHARACTER = /[\p{L}\p{N}]/u;

/** A paragraph carries `ordered: null`; a list carries its numbering style. */
type RawBlock = { ordered: boolean | null; parts: string[] };

/**
 * `streaming` marks text that is still arriving. An emphasis marker whose
 * partner has not been received yet then styles the tail instead of showing
 * the visitor a bare `**` that disappears a moment later. Once the answer is
 * complete an unmatched marker is what the model wrote, so it stays literal.
 */
export function parseRichText(raw: string, streaming = false): RichBlock[] {
  const blocks: RawBlock[] = [];
  let current: RawBlock | null = null;

  for (const line of raw.replace(/\r\n?/g, "\n").split("\n")) {
    if (!line.trim()) {
      current = null;
      continue;
    }
    const bullet = BULLET_ITEM.exec(line);
    const numbered = bullet ? null : NUMBERED_ITEM.exec(line);
    const item = bullet ?? numbered;
    if (item) {
      const ordered = numbered !== null;
      if (current === null || current.ordered !== ordered) {
        current = { ordered, parts: [] };
        blocks.push(current);
      }
      current.parts.push(item[1]);
      continue;
    }
    if (current !== null && current.ordered !== null) {
      // A wrapped bullet keeps belonging to the item it continues.
      current.parts[current.parts.length - 1] += ` ${line.trim()}`;
      continue;
    }
    if (current === null) {
      current = { ordered: null, parts: [] };
      blocks.push(current);
    }
    current.parts.push(line);
  }

  return blocks.map((block, index) => toBlock(block, streaming && index === blocks.length - 1));
}

function toBlock(block: RawBlock, open: boolean): RichBlock {
  if (block.ordered === null) {
    return { kind: "paragraph", inlines: parseInline(block.parts.join("\n"), open) };
  }
  const last = block.parts.length - 1;
  return {
    kind: "list",
    ordered: block.ordered,
    items: block.parts.map((item, index) => parseInline(item, open && index === last)),
  };
}

function parseInline(raw: string, open: boolean): InlineNode[] {
  const nodes: InlineNode[] = [];
  let plain = "";
  let index = 0;

  const flush = () => {
    if (plain) nodes.push({ kind: "text", text: plain });
    plain = "";
  };
  const emphasize = (marker: string, text: string) => {
    if (text) nodes.push({ kind: marker === "**" ? "strong" : "em", text });
  };

  while (index < raw.length) {
    const marker = openerAt(raw, index);
    if (marker) {
      const start = index + marker.length;
      const end = closerIndex(raw, start, marker);
      if (end !== -1) {
        flush();
        emphasize(marker, raw.slice(start, end));
        index = end + marker.length;
        continue;
      }
      if (open) {
        flush();
        emphasize(marker, raw.slice(start));
        return nodes;
      }
    }
    plain += raw[index];
    index += 1;
  }

  flush();
  return nodes;
}

/** An opener starts a word and is followed by content. */
function openerAt(raw: string, index: number): string | null {
  const character = raw[index];
  const marker = raw.startsWith("**", index)
    ? "**"
    : character === "*" || character === "_"
      ? character
      : null;
  if (marker === null) return null;
  const before = index > 0 ? raw[index - 1] : "";
  const after = raw[index + marker.length];
  if (!after || after === "*" || /\s/.test(after)) return null;
  // Rejecting an intraword opener keeps identifiers such as
  // AZURE_OPENAI_API_LLM_KEY and a multiplication sign out of the parser.
  return before && WORD_CHARACTER.test(before) ? null : marker;
}

/** A closer ends a word and, for single markers, does not sit inside one. */
function closerIndex(raw: string, from: number, marker: string): number {
  for (let index = raw.indexOf(marker, from); index !== -1; index = raw.indexOf(marker, index + 1)) {
    if (index === from) continue;
    if (/\s/.test(raw[index - 1])) continue;
    const after = raw[index + marker.length];
    if (marker !== "**" && after && WORD_CHARACTER.test(after)) continue;
    return index;
  }
  return -1;
}
