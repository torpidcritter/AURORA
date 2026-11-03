import { describe, expect, it } from "vitest";

import { fmtLift, promotionBody, type PromotionForm } from "@/lib/promotions";

describe("promotion form", () => {
  const ok: PromotionForm = {
    name: " Summer ",
    start_date: "2099-07-01",
    end_date: "2099-07-14",
    scope: "products",
    product_ids: ["a", "", "a", "b"],
    category: "Tea",
    discount_pct: "20",
    uplift: "",
    notes: " flyer ",
  };

  it("builds the request and asks for an estimate when no uplift is given", () => {
    expect(promotionBody(ok)).toEqual({
      body: {
        name: "Summer",
        start_date: "2099-07-01",
        end_date: "2099-07-14",
        product_ids: ["a", "b"],
        category: null,
        discount_pct: 20,
        uplift: null,
        use_estimate: true,
        notes: "flyer",
      },
    });
    const own = promotionBody({ ...ok, scope: "category", uplift: "1.8", discount_pct: "" });
    expect(own).toMatchObject({ body: { product_ids: [], category: "Tea", uplift: 1.8, use_estimate: false, discount_pct: null } });
  });

  it("explains what is wrong", () => {
    expect(promotionBody({ ...ok, name: " " })).toEqual({ error: "Give the promotion a name" });
    expect(promotionBody({ ...ok, end_date: "2099-06-30" })).toEqual({ error: "The end date must be on or after the start date" });
    expect(promotionBody({ ...ok, end_date: "2099-10-15" })).toEqual({ error: "A promotion can last at most 92 days" });
    expect(promotionBody({ ...ok, product_ids: [""] })).toEqual({ error: "Choose at least one product" });
    expect(promotionBody({ ...ok, scope: "category", category: "" })).toEqual({ error: "Choose a category" });
    expect("error" in promotionBody({ ...ok, discount_pct: "120" })).toBe(true);
    expect("error" in promotionBody({ ...ok, uplift: "0" })).toBe(true);
    expect("error" in promotionBody({ ...ok, uplift: "11" })).toBe(true);
  });

  it("formats lifts", () => {
    expect(fmtLift(2.4)).toBe("×2.4 (+140%)");
    expect(fmtLift(0.8)).toBe("×0.8 (-20%)");
    expect(fmtLift(3)).toBe("×3 (+200%)");
    expect(fmtLift(null)).toBe("—");
  });
});
