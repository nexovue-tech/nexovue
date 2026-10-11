import { describe, expect, it } from "vitest";

import { ACCOUNT_DELETED, PASSWORD_WAS_RESET, parseLoginSearch, parseTokenSearch } from "./search";

describe("parseTokenSearch", () => {
  it("takes the token a recovery link carries", () => {
    expect(parseTokenSearch({ token: "abc" })).toEqual({ token: "abc" });
  });

  it("treats an absent, empty or non-string token as no token at all", () => {
    // Each of these reaches a screen that must not post an empty token: a mail client
    // that cut the link in half, a hand-typed path, a crawler.
    expect(parseTokenSearch({})).toEqual({});
    expect(parseTokenSearch({ token: "" })).toEqual({});
    expect(parseTokenSearch({ token: 42 })).toEqual({});
    expect(parseTokenSearch({ token: ["a", "b"] })).toEqual({});
  });
});

describe("parseLoginSearch", () => {
  it("recognises the notices another screen may ask for", () => {
    expect(parseLoginSearch({ notice: PASSWORD_WAS_RESET })).toEqual({
      notice: PASSWORD_WAS_RESET,
    });
    expect(parseLoginSearch({ notice: ACCOUNT_DELETED })).toEqual({ notice: ACCOUNT_DELETED });
  });

  it("drops anything else, so a pasted address cannot make the screen say arbitrary words", () => {
    expect(parseLoginSearch({ notice: "Your account has been closed" })).toEqual({});
    expect(parseLoginSearch({})).toEqual({});
  });
});
