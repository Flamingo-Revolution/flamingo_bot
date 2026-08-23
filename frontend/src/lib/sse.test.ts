import { describe, expect, it } from "vitest";

import { consumeEventStream, parseEventBlock, type ServerEvent } from "./sse";

describe("parseEventBlock", () => {
  it("parses typed JSON events", () => {
    expect(parseEventBlock('event: delta\ndata: {"text":"Përshëndetje"}')).toEqual({
      event: "delta",
      data: { text: "Përshëndetje" },
    });
  });

  it("joins multiline data fields", () => {
    expect(parseEventBlock("event: message\ndata: first\ndata: second")).toEqual({
      event: "message",
      data: "first\nsecond",
    });
  });
});

describe("consumeEventStream", () => {
  it("handles events split across byte chunks", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('event: citation\ndata: {"id":"S1"'));
        controller.enqueue(encoder.encode("}\n\nevent: done\ndata: {}\n\n"));
        controller.close();
      },
    });
    const events: ServerEvent[] = [];

    await consumeEventStream(stream, (event) => events.push(event));

    expect(events).toEqual([
      { event: "citation", data: { id: "S1" } },
      { event: "done", data: {} },
    ]);
  });
});
