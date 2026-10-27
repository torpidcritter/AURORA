import { describe, expect, it } from "vitest";

import { auditParams, dateRangeError } from "@/lib/audit";

const empty = { action: "", actor: "", outcome: "", from: "", to: "" };

describe("audit filters", () => {
  it("builds list and export parameters with whole UTC days", () => {
    expect(auditParams(empty).toString()).toBe("");
    const qs = auditParams({ action: " product. ", actor: "a@b.co", outcome: "failure", from: "2026-10-01", to: "2026-10-31" });
    expect(qs.get("action")).toBe("product.");
    expect(qs.get("actor")).toBe("a@b.co");
    expect(qs.get("outcome")).toBe("failure");
    expect(qs.get("since")).toBe("2026-10-01T00:00:00Z");
    expect(qs.get("until")).toBe("2026-11-01T00:00:00.000Z"); // the end day is included
  });

  it("rejects an end date before the start", () => {
    expect(dateRangeError("2026-10-02", "2026-10-01")).toMatch(/end date/);
    expect(dateRangeError("2026-10-01", "2026-10-01")).toBeNull();
    expect(dateRangeError("", "2026-10-01")).toBeNull();
  });
});
