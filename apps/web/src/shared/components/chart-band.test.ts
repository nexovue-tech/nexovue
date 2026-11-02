import { describe, expect, it } from "vitest";

import { bandStretches } from "./chart-band";

describe("a band's stretches", () => {
  it("is one stretch while both edges are known", () => {
    expect(
      bandStretches(
        [
          [1, 2, 3],
          [5, 6, 7],
          [1, 1, 1],
        ],
        { upper: 1, lower: 2 },
      ),
    ).toEqual([{ xs: [1, 2, 3], upper: [5, 6, 7], lower: [1, 1, 1] }]);
  });

  it("breaks where either edge is missing, rather than drawing across the gap", () => {
    expect(
      bandStretches(
        [
          [1, 2, 3, 4, 5],
          [5, null, 7, 8, 9],
          [1, 1, 1, null, 1],
        ],
        { upper: 1, lower: 2 },
      ),
    ).toEqual([
      { xs: [1], upper: [5], lower: [1] },
      { xs: [3], upper: [7], lower: [1] },
      { xs: [5], upper: [9], lower: [1] },
    ]);
  });

  it("is nothing when the rows it names are absent", () => {
    expect(bandStretches([[1, 2]], { upper: 1, lower: 2 })).toEqual([]);
  });
});
