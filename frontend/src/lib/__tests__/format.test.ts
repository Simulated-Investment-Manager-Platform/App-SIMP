import { costColor, mix, series } from "@/lib/colors";
import { bps, pct, usd } from "@/lib/format";

describe("format", () => {
  it("formats dollars with units", () => {
    expect(usd(21_070_000_000)).toBe("$21.07bn");
    expect(usd(82_500)).toBe("$83k");
    expect(usd(-103_700_000)).toBe("−$103.7m");
    expect(usd(null)).toBe("—");
  });
  it("formats costs as signed bps (positive = worse)", () => {
    expect(bps(6.62)).toBe("+6.6");
    expect(bps(-30.2)).toBe("−30.2");
    expect(bps(0)).toBe("0.0");
    expect(bps(Number.NaN)).toBe("—");
  });
  it("formats percentages", () => {
    expect(pct(0.847, 1)).toBe("84.7%");
  });
});

describe("colors", () => {
  it("assigns categorical slots in fixed order, buy blue then sell orange", () => {
    expect(series(0, "light")).toBe("#2a78d6");
    expect(series(1, "light")).toBe("#eb6834");
    expect(series(8, "dark")).toBe(series(0, "dark"));
  });
  it("uses a neutral midpoint for zero cost and opposite poles for sign", () => {
    expect(costColor(0, 30, "dark")).toBe(mix("#383835", "#383835", 0));
    expect(costColor(30, 30, "dark")).toBe("rgb(230,103,103)");
    expect(costColor(-30, 30, "dark")).toBe("rgb(57,135,229)");
    expect(costColor(null)).toBe("transparent");
  });
});
