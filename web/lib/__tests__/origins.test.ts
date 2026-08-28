/**
 * THE ORIGIN ALLOW-LIST, and the fail-closed direction it must take.
 *
 * Small file, and the reason it exists is a real hazard rather than completeness:
 * `originsFrom` returning `[]` in a deployment means `originIsAcceptable` refuses
 * every present `Origin`, so **every approval is refused and the button looks
 * broken**. The two directions of that failure want opposite fixes, and only a test
 * distinguishes them.
 */

import { describe, expect, it } from "vitest";

import { originIsAcceptable } from "../authz";
import { originsFrom } from "../origins";

describe("deriving the allow-list", () => {
  it("yields the normalised origin of a configured URL", () => {
    expect(originsFrom("https://app.example")).toEqual(["https://app.example"]);
  });

  it.each([
    "https://app.example/",
    "https://app.example/dashboard",
    "https://app.example/?next=/runs",
    "https://app.example:443",
  ])("normalises %j to the bare origin a browser would send", (configured) => {
    // A BROWSER SENDS THE BARE ORIGIN — no path, no query, no default port. If this
    // compared raw configuration text, a trailing slash left on an environment
    // variable would refuse every legitimate click, and the symptom would read as a
    // broken button rather than as a misconfiguration.
    expect(originsFrom(configured)).toEqual(["https://app.example"]);
  });

  it("keeps a non-default port, because the origin genuinely includes it", () => {
    expect(originsFrom("http://localhost:3000")).toEqual(["http://localhost:3000"]);
  });

  it("does NOT normalise away the scheme", () => {
    // `http` and `https` are different origins. Folding them would accept a
    // downgrade an attacker chooses.
    expect(originsFrom("http://app.example")).toEqual(["http://app.example"]);
    expect(originsFrom("http://app.example")).not.toEqual(["https://app.example"]);
  });
});

describe("the fail-closed direction", () => {
  it.each([undefined, "", "   "])(
    "yields an EMPTY list for %j rather than a permissive one",
    (configured) => {
      expect(originsFrom(configured)).toEqual([]);
    },
  );

  it("yields an empty list for a malformed URL rather than throwing", () => {
    // Throwing here would 500 every request, including the GETs that mutate
    // nothing. A refused mutation carrying a message is the better failure.
    expect(originsFrom("not-a-url")).toEqual([]);
    expect(originsFrom("://missing-scheme")).toEqual([]);
  });

  it("an empty list REFUSES a present Origin, and admits an absent one", () => {
    // THE TWO HALVES OF THE FAIL-CLOSED BEHAVIOUR, asserted together because they
    // are one decision: nothing configured must not mean allow-all (a browser POST
    // is refused), and must not mean refuse-all either (curl and the documented CLI
    // fallback send no Origin and have to keep working).
    expect(originIsAcceptable("https://app.example", originsFrom(""))).toBe(false);
    expect(originIsAcceptable(null, originsFrom(""))).toBe(true);
  });

  it("a configured list admits its own origin and refuses every other", () => {
    const allowed = originsFrom("https://app.example");
    // Guard: if `originsFrom` ever returned [] here, both assertions below would
    // still pass for the WRONG reason — the second trivially, the first not at all.
    // So the list is asserted non-empty first.
    expect(allowed.length).toBe(1);
    expect(originIsAcceptable("https://app.example", allowed)).toBe(true);
    expect(originIsAcceptable("https://evil.example", allowed)).toBe(false);
  });
});
