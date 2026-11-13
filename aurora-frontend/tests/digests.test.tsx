import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DigestScheduleForm } from "@/components/DigestScheduleForm";
import { digestBody, digestErrors, emptyDigest, whenLabel } from "@/lib/digests";

describe("digest helpers", () => {
  it("describes when a digest is sent", () => {
    expect(whenLabel({ frequency: "daily", weekday: null, hour_utc: 7 })).toBe("Daily at 07:00 UTC");
    expect(whenLabel({ frequency: "weekly", weekday: 0, hour_utc: 18 })).toBe("Mondays at 18:00 UTC");
  });

  it("checks the form before sending", () => {
    const f = { ...emptyDigest(), name: "Morning" };
    expect(digestErrors(f)).toBeNull();
    expect(digestErrors({ ...f, name: " " })).toMatch(/name/);
    expect(digestErrors({ ...f, sections: [] })).toMatch(/section/);
    expect(digestErrors({ ...f, channel: "slack", target: "" })).toBe("Enter the slack webhook url");
    expect(digestErrors({ ...f, channel: "webhook", target: "http://x.example.com" })).toMatch(/https/);
    expect(digestErrors({ ...f, channel: "slack", target: "https://example.com/x" })).toMatch(/hooks.slack.com/);
    expect(digestErrors({ ...f, channel: "email", target: "a@example.com" })).toBeNull();
  });

  it("sends a weekday only for weekly digests and no target in-app", () => {
    const f = { ...emptyDigest(), name: " Morning ", weekday: 3, target: "ignored" };
    expect(digestBody(f)).toMatchObject({ name: "Morning", weekday: null, target: null, channel: "in_app" });
    expect(digestBody({ ...f, frequency: "weekly", channel: "slack", target: " https://hooks.slack.com/x " })).toMatchObject({
      weekday: 3,
      target: "https://hooks.slack.com/x",
    });
  });
});

describe("DigestScheduleForm", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: "s1" }), { status: 201, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("creates a weekly Slack digest", async () => {
    const onCreated = vi.fn();
    render(<DigestScheduleForm onCreated={onCreated} />);
    fireEvent.change(screen.getByLabelText("Digest name"), { target: { value: "Weekly buying" } });
    fireEvent.change(screen.getByLabelText("How often"), { target: { value: "weekly" } });
    fireEvent.change(screen.getByLabelText("Day"), { target: { value: "4" } });
    fireEvent.click(screen.getByLabelText("Overstock"));
    fireEvent.change(screen.getByLabelText("Send to"), { target: { value: "slack" } });
    fireEvent.change(screen.getByLabelText("Slack webhook URL"), { target: { value: "https://example.com/hook" } });
    fireEvent.click(screen.getByRole("button", { name: "Create digest" }));
    expect(await screen.findByText("Slack webhook URLs start with https://hooks.slack.com/")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Slack webhook URL"), { target: { value: "https://hooks.slack.com/services/x" } });
    fireEvent.click(screen.getByRole("button", { name: "Create digest" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/proxy/digests/schedules");
    expect(JSON.parse(init.body)).toEqual({
      name: "Weekly buying",
      frequency: "weekly",
      weekday: 4,
      hour_utc: 7,
      sections: ["stockouts", "alerts", "purchasing", "overstock"],
      channel: "slack",
      target: "https://hooks.slack.com/services/x",
    });
  });
});
