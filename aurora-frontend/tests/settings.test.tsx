import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrgSettingsForm } from "@/components/OrgSettingsForm";
import { ApiError, api } from "@/lib/api";
import { boundsHint, buildSettingsPatch, fieldErrorsFromApi, formatSettingValue, inputValue, parseSettingInput } from "@/lib/settings";
import type { OrgSetting, OrgSettings } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api")>();
  return { ...mod, api: vi.fn() };
});

const base = { minimum: null, maximum: null, minimum_exclusive: false, maximum_exclusive: false, choices: null, unit: null, override: null, source: "default" as const };
const lead: OrgSetting = { ...base, key: "default_lead_time_days", label: "Default supplier lead time", description: "d", group: "inventory", type: "integer", minimum: 1, maximum: 365, unit: "days", value: 7, default: 7 };
const sl: OrgSetting = { ...base, key: "default_service_level", label: "Default cycle service level", description: "d", group: "inventory", type: "number", minimum: 0.5, minimum_exclusive: true, maximum: 0.999, value: 0.975, default: 0.95, override: 0.975, source: "org" };
const live: OrgSetting = { ...base, key: "accuracy_live_threshold", label: "Live accuracy alert threshold", description: "d", group: "monitoring", type: "number", minimum: 1, maximum: 500, unit: "%", value: 25, default: 25 };
const sev: OrgSetting = { ...base, key: "alert_min_severity", label: "Minimum severity to notify", description: "d", group: "alerts", type: "choice", choices: ["low", "medium", "high", "critical"], value: "high", default: "high" };
const items = [lead, sl, live, sev];
const data: OrgSettings = { items, alert_channels: ["log"] };

describe("settings helpers", () => {
  it("formats values with their units", () => {
    expect(formatSettingValue(lead, 7)).toBe("7 days");
    expect(formatSettingValue(lead, 1)).toBe("1 day");
    expect(formatSettingValue(sl, 0.975)).toBe("0.975 (97.5%)");
    expect(formatSettingValue(live, 25)).toBe("25%");
    expect(formatSettingValue(sev, "critical")).toBe("Critical");
    expect(formatSettingValue(lead, null)).toBe("—");
  });

  it("describes the bounds, including exclusive ones", () => {
    expect(boundsHint(lead)).toBe("1–365 days, whole number");
    expect(boundsHint(sl)).toBe("Above 0.5, up to 0.999");
    expect(boundsHint(live)).toBe("1–500%");
    expect(boundsHint(sev)).toBe("");
  });

  it.each([
    [lead, "10", 10],
    [lead, " 365 ", 365],
    [sl, "0.999", 0.999],
    [sl, "0.51", 0.51],
    [sev, "low", "low"],
  ] as const)("accepts %s = %j", (s, raw, value) => {
    expect(parseSettingInput(s, raw)).toEqual({ value });
  });

  it.each([
    [lead, "", /Enter a value/],
    [lead, "abc", /number/],
    [lead, "7.5", /whole number/],
    [lead, "0", /1–365/],
    [lead, "366", /1–365/],
    [sl, "0.5", /Above 0.5/],
    [sl, "1", /up to 0.999/],
    [live, "Infinity", /number/],
    [sev, "urgent", /Choose one of: Low, Medium, High, Critical/],
  ] as const)("rejects %s = %j", (s, raw, msg) => {
    expect(parseSettingInput(s, raw).error).toMatch(msg);
  });

  it("builds a patch with only real changes and null for resets", () => {
    expect(buildSettingsPatch(items, {})).toEqual({ body: {}, errors: {} });
    // typing the value already in use is not a change
    expect(buildSettingsPatch(items, { default_lead_time_days: "7" }).body).toEqual({});
    expect(buildSettingsPatch(items, { default_lead_time_days: "10", alert_min_severity: "medium" }).body).toEqual({
      default_lead_time_days: 10,
      alert_min_severity: "medium",
    });
    // resetting an override sends null; resetting a default is a no-op
    expect(buildSettingsPatch(items, { default_service_level: null, default_lead_time_days: null }).body).toEqual({ default_service_level: null });
    const bad = buildSettingsPatch(items, { default_lead_time_days: "0", accuracy_live_threshold: "30" });
    expect(bad.errors).toEqual({ default_lead_time_days: expect.stringMatching(/1–365/) });
    expect(bad.body).toEqual({ accuracy_live_threshold: 30 });
  });

  it("shows the default in the input while a reset is pending", () => {
    expect(inputValue(sl, {})).toBe("0.975");
    expect(inputValue(sl, { default_service_level: null })).toBe("0.95");
    expect(inputValue(sl, { default_service_level: "0.9" })).toBe("0.9");
  });

  it("maps API validation details to fields", () => {
    const e = new ApiError(422, "validation_error", "Request validation failed", [
      { loc: ["body", "default_lead_time_days"], msg: "Input should be greater than or equal to 1" },
      { loc: ["body", "nope"], msg: "Extra inputs are not permitted" },
    ]);
    expect(fieldErrorsFromApi(e, items.map((s) => s.key))).toEqual({ default_lead_time_days: "Input should be greater than or equal to 1" });
    expect(fieldErrorsFromApi(new ApiError(403, "forbidden", "no"), ["x"])).toEqual({});
    expect(fieldErrorsFromApi(new Error("x"), ["x"])).toEqual({});
  });
});

describe("OrgSettingsForm", () => {
  const mockedApi = vi.mocked(api);
  // Braces matter: a function returned from beforeEach is run as a teardown hook.
  beforeEach(() => {
    mockedApi.mockReset();
  });

  it("is read-only without manage_settings", () => {
    render(<OrgSettingsForm data={data} canEdit={false} onSaved={vi.fn()} />);
    expect(screen.getByText(/Only administrators can change them/)).toBeInTheDocument();
    expect(screen.getByLabelText("Default supplier lead time")).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Save changes/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Reset to default/ })).toBeNull();
    expect(screen.getByText("Overridden")).toBeInTheDocument();
    expect(screen.getAllByText("Default").length).toBe(3);
  });

  it("shows client validation messages and does not call the API", async () => {
    render(<OrgSettingsForm data={data} canEdit onSaved={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Default supplier lead time"), { target: { value: "400" } });
    fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));
    expect(await screen.findByText(/Allowed: 1–365 days/)).toBeInTheDocument();
    expect(mockedApi).not.toHaveBeenCalled();
  });

  it("saves changes and resets as one PATCH", async () => {
    const updated: OrgSettings = { ...data, items: [{ ...lead, value: 10, override: 10, source: "org" }, { ...sl, value: 0.95, override: null, source: "default" }, live, sev] };
    mockedApi.mockResolvedValue(updated);
    const onSaved = vi.fn();
    render(<OrgSettingsForm data={data} canEdit onSaved={onSaved} />);
    fireEvent.change(screen.getByLabelText("Default supplier lead time"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: /Reset to default/ }));
    expect(screen.getByText(/2 unsaved changes/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(updated));
    expect(mockedApi).toHaveBeenCalledWith("/settings/org", { method: "PATCH", body: { default_lead_time_days: 10, default_service_level: null } });
    expect(await screen.findByText("Saved 2 changes.")).toBeInTheDocument();
  });

  it("shows the API's validation message next to the field", async () => {
    mockedApi.mockRejectedValue(
      new ApiError(422, "validation_error", "Request validation failed", [{ loc: ["body", "accuracy_live_threshold"], msg: "Input should be less than or equal to 500" }]),
    );
    render(<OrgSettingsForm data={data} canEdit onSaved={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Live accuracy alert threshold"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));
    expect(await screen.findByText("Input should be less than or equal to 500")).toBeInTheDocument();
    expect(screen.getByText(/server rejected some values/)).toBeInTheDocument();
  });

  it("explains where alerts are sent", () => {
    render(<OrgSettingsForm data={data} canEdit onSaved={vi.fn()} />);
    expect(screen.getByText(/No webhook is configured/)).toBeInTheDocument();
  });
});
