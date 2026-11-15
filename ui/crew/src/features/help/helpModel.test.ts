import { describe, expect, it } from "vitest";
import { assistantWords, cachedFirstScreen, splitQuestion } from "./helpModel";

describe("Someone needs help", () => {
  it("has its first screen from the bundled configuration, with no hub call", () => {
    const first = cachedFirstScreen();
    expect(first.nodeId).toBe("air_safe");
    expect(first.question).toBe("Is the air safe?");
    expect(first.answers.length).toBeGreaterThan(1);
  });

  it("says the assistant is stopped and that help works without it", () => {
    const words = assistantWords("stopped");
    expect(words.model).toBe("Assistant: stopped");
    expect(words.works).toBe("Works without the assistant");
  });

  it("drops the stopped swatch while the model runs but keeps the works-without line", () => {
    const words = assistantWords("waiting");
    expect(words.model).toBeNull();
    expect(words.works).toBe("Works without the assistant");
  });

  it("splits a question from its hint", () => {
    expect(splitQuestion("Is it? Look.")).toEqual(["Is it?", "Look."]);
  });
});
