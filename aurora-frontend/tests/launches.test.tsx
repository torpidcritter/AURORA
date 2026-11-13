import { describe, expect, it } from "vitest";

import { launchBody, launchStage } from "@/lib/launches";

describe("launch plan form", () => {
  const ok = { launch_date: "2026-12-01", analogue_ids: ["a", "", "a", "b"], scale_pct: "75", notes: " new tin " };

  it("builds the request, dropping blanks and repeats", () => {
    expect(launchBody(ok)).toEqual({ body: { launch_date: "2026-12-01", analogue_ids: ["a", "b"], scale: 0.75, notes: "new tin" } });
  });

  it("explains what is missing", () => {
    expect(launchBody({ ...ok, launch_date: "" })).toEqual({ error: "Choose the launch date" });
    expect(launchBody({ ...ok, analogue_ids: [""] })).toEqual({ error: "Choose at least one similar product" });
    expect(launchBody({ ...ok, analogue_ids: ["1", "2", "3", "4", "5", "6"] })).toEqual({ error: "Choose at most 5 similar products" });
    expect("error" in launchBody({ ...ok, scale_pct: "0" })).toBe(true);
    expect("error" in launchBody({ ...ok, scale_pct: "abc" })).toBe(true);
  });

  it("describes the stage of a launch", () => {
    expect(launchStage(0, 56, "2099-01-01", "2026-11-03")).toBe("Not launched yet");
    expect(launchStage(0, 56, "2026-11-01", "2026-11-03")).toMatch(/waiting for the first sales/);
    expect(launchStage(10, 56, "2026-10-01", "2026-11-03")).toMatch(/10 day/);
    expect(launchStage(56, 56, "2026-08-01", "2026-11-03")).toMatch(/normal forecasting/);
  });
});
