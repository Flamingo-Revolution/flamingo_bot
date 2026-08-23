import { describe, expect, it } from "vitest";

import { parseRichText, type InlineNode, type RichBlock } from "./richtext";

const flatten = (nodes: InlineNode[]) => nodes.map((node) => `${node.kind}:${node.text}`);

const inlinesOf = (block: RichBlock) =>
  block.kind === "paragraph" ? block.inlines : block.items.flat();

describe("parseRichText", () => {
  it("renders bold and italic as nodes instead of literal markers", () => {
    const [block] = parseRichText("Kërkohet **transparencë** dhe *pjesëmarrje*.");

    expect(flatten(inlinesOf(block))).toEqual([
      "text:Kërkohet ",
      "strong:transparencë",
      "text: dhe ",
      "em:pjesëmarrje",
      "text:.",
    ]);
  });

  it("keeps a bullet list with emphasis inside its items", () => {
    const blocks = parseRichText(
      "Kërkesat kryesore:\n\n- **Ndalimin e ndërtimeve** në zonat e mbrojtura [S1].\n- **Dorëheqjen e qeverisë** [S3].",
    );

    expect(blocks.map((block) => block.kind)).toEqual(["paragraph", "list"]);
    const list = blocks[1];
    expect(list.kind === "list" && list.ordered).toBe(false);
    expect(list.kind === "list" && list.items.map((item) => flatten(item)[0])).toEqual([
      "strong:Ndalimin e ndërtimeve",
      "strong:Dorëheqjen e qeverisë",
    ]);
  });

  it("separates a numbered list from a bullet list", () => {
    const blocks = parseRichText("1. Së pari\n2. Së dyti\n\n- Shënim");

    expect(blocks.map((block) => block.kind === "list" && block.ordered)).toEqual([true, false]);
  });

  it("keeps a wrapped bullet inside the item it continues", () => {
    const blocks = parseRichText("- Ndalimin e ndërtimeve\n  në zonat e mbrojtura");

    expect(blocks).toHaveLength(1);
    expect(flatten(inlinesOf(blocks[0]))).toEqual([
      "text:Ndalimin e ndërtimeve në zonat e mbrojtura",
    ]);
  });

  it("leaves an unmatched marker literal once the answer is complete", () => {
    const [block] = parseRichText("Rreth 3 * 4 vullnetarë dhe një **hapje");

    expect(flatten(inlinesOf(block))).toEqual(["text:Rreth 3 * 4 vullnetarë dhe një **hapje"]);
  });

  it("styles the tail of a marker whose partner has not streamed in yet", () => {
    const [block] = parseRichText("Kërkesat: **Ndalimin e ndërt", true);

    expect(flatten(inlinesOf(block))).toEqual(["text:Kërkesat: ", "strong:Ndalimin e ndërt"]);
  });

  it("applies the streaming rule only to the block still arriving", () => {
    const blocks = parseRichText("Një **hapje\n\n- ende duke ardhur *tani", true);

    expect(flatten(inlinesOf(blocks[0]))).toEqual(["text:Një **hapje"]);
    expect(flatten(inlinesOf(blocks[1]))).toEqual(["text:ende duke ardhur ", "em:tani"]);
  });

  it("does not italicize the underscores inside an identifier", () => {
    const [block] = parseRichText("AZURE_OPENAI_API_LLM_KEY=sk-live-example");

    expect(flatten(inlinesOf(block))).toEqual(["text:AZURE_OPENAI_API_LLM_KEY=sk-live-example"]);
  });

  it("keeps citation markers as visible text", () => {
    const [block] = parseRichText("Një lëvizje qytetare [S1][S2].");

    expect(flatten(inlinesOf(block))).toEqual(["text:Një lëvizje qytetare [S1][S2]."]);
  });

  it("returns no blocks for empty text", () => {
    expect(parseRichText("   \n\n  ")).toEqual([]);
  });
});
