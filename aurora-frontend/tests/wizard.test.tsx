import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import UploadPage from "@/app/(app)/upload/page";
import { parseEpochs } from "@/components/TrainForm";
import { fetcher } from "@/lib/api";
import type { Dataset, Role } from "@/lib/types";
import { trainGate, wizardSteps } from "@/lib/wizard";

const session = vi.hoisted(() => ({ role: "analyst" as string, permissions: [] as string[] }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    me: { role: session.role },
    loading: false,
    can: (p: string) => session.permissions.includes(p),
  }),
}));

const ROLE_PERMS: Record<Role, string[]> = {
  viewer: ["read"],
  operator: ["read", "upload_data", "manage_inventory", "acknowledge_alerts"],
  analyst: ["read", "upload_data", "manage_inventory", "acknowledge_alerts", "train_models", "tag_datasets"],
  admin: ["read", "upload_data", "manage_inventory", "acknowledge_alerts", "train_models", "tag_datasets", "manage_users"],
};
const asRole = (r: Role) => {
  session.role = r;
  session.permissions = ROLE_PERMS[r];
};

const imported = { id: "d1", status: "imported" as const };
const rejected = { id: "d2", status: "rejected" as const };

describe("trainGate", () => {
  it("explains that roles without train_models cannot train, whatever the progress", () => {
    for (const input of [
      { dataset: null, reviewed: false },
      { dataset: imported, reviewed: true },
    ]) {
      const g = trainGate({ canTrain: false, role: "operator", ...input });
      expect(g.status).toBe("forbidden");
      expect(g.reason).toMatch(/train-models permission, which the analyst and admin roles have/);
      expect(g.reason).toMatch(/Your role \(operator\) cannot start training/);
    }
  });

  it("stays locked until an imported dataset has been reviewed", () => {
    expect(trainGate({ canTrain: true, dataset: null, reviewed: false })).toMatchObject({ status: "locked", reason: expect.stringMatching(/Upload a sales file/) });
    expect(trainGate({ canTrain: true, dataset: rejected, reviewed: true })).toMatchObject({ status: "locked", reason: expect.stringMatching(/rejected/) });
    expect(trainGate({ canTrain: true, dataset: imported, reviewed: false })).toMatchObject({ status: "locked", reason: expect.stringMatching(/Review the quality report/) });
    expect(trainGate({ canTrain: true, dataset: imported, reviewed: true })).toEqual({ status: "ready", reason: null });
  });
});

describe("wizardSteps", () => {
  const states = (input: Parameters<typeof wizardSteps>[0]) => wizardSteps(input).map((s) => s.state);
  it("walks upload -> review -> train", () => {
    expect(states({ canTrain: true, dataset: null, reviewed: false })).toEqual(["current", "upcoming", "upcoming"]);
    expect(states({ canTrain: true, dataset: imported, reviewed: false })).toEqual(["done", "current", "upcoming"]);
    expect(states({ canTrain: true, dataset: imported, reviewed: true })).toEqual(["done", "done", "current"]);
    expect(states({ canTrain: true, dataset: imported, reviewed: true, trainingFinished: true })).toEqual(["done", "done", "done"]);
    expect(states({ canTrain: true, dataset: rejected, reviewed: false })).toEqual(["current", "upcoming", "upcoming"]);
  });
  it("marks training unavailable for roles without the permission", () => {
    expect(states({ canTrain: false, dataset: imported, reviewed: true })).toEqual(["done", "done", "unavailable"]);
  });
});

describe("parseEpochs", () => {
  it.each([["30", 30], [" 3 ", 3], ["1", 1], ["200", 200]])("accepts %j", (raw, n) => expect(parseEpochs(raw)).toBe(n));
  it.each(["", "0", "201", "2.5", "abc", "-1"])("rejects %j", (raw) => expect(parseEpochs(raw)).toBeNull());
});

const dataset = (o: Partial<Dataset> = {}): Dataset => ({
  id: "ds-1",
  filename: "sales.csv",
  file_format: "csv",
  size_bytes: 1200,
  status: "imported",
  tags: {},
  missing_policy: "zero",
  row_count: 100,
  accepted_rows: 98,
  rejected_rows: 2,
  quality_score: 96.5,
  quality_report: { products: 3, date_min: "2026-01-01", date_max: "2026-09-28", quality_formula: "100 - penalties", warnings: [] },
  column_mapping: { date: "date", sku: "sku", quantity: "qty" },
  created_at: "2026-10-01T10:00:00Z",
  ...o,
});

const job = (o: Record<string, unknown> = {}) => ({
  id: "job-1", kind: "train", status: "queued", progress: 0, params: {}, result: null, error: null, attempts: 0, cancel_requested: false,
  created_at: "2026-10-01T10:00:00Z", started_at: null, finished_at: null, heartbeat_at: null, ...o,
});

describe("upload page wizard", () => {
  const fetchMock = vi.fn();
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  let uploaded: Dataset;

  beforeEach(() => {
    uploaded = dataset();
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
      const u = new URL(url, "http://x");
      const method = init?.method ?? "GET";
      if (u.pathname === "/api/proxy/datasets" && method === "GET") return json({ items: [], total: 0, limit: 10, offset: 0 });
      if (u.pathname === "/api/proxy/upload-dataset" && method === "POST") return json({ dataset: uploaded, duplicate: false });
      if (u.pathname === "/api/proxy/jobs" && method === "GET") return json({ items: [], total: 0, limit: 1, offset: 0 });
      if (u.pathname === "/api/proxy/train" && method === "POST") return json(job(), 202);
      if (u.pathname === "/api/proxy/train/job-1/status") return json(job());
      throw new Error(`unexpected ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const renderPage = () =>
    render(
      <SWRConfig value={{ provider: () => new Map(), fetcher, dedupingInterval: 0, shouldRetryOnError: false }}>
        <UploadPage />
      </SWRConfig>,
    );

  const upload = async () => {
    const file = new File(["date,sku,qty\n2026-01-01,A,1\n"], "sales.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("File"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload and validate" }));
    await screen.findByText("96.5 / 100");
  };
  const trainCard = () => screen.getByRole("heading", { name: /3\. Train a forecasting model/ }).closest("section")!;

  it("lets an analyst train only after the upload has been reviewed, then follows the job", async () => {
    asRole("analyst");
    renderPage();
    expect(within(trainCard()).getByText(/Upload a sales file in step 1/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start training" })).toBeNull();

    await upload();
    expect(within(trainCard()).getByText(/Review the quality report in step 2/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start training" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Continue to training" }));
    expect(screen.getByText(/Quality reviewed/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Start training" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([u, i]) => u === "/api/proxy/train" && i?.method === "POST")).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([u, i]) => u === "/api/proxy/train" && i?.method === "POST")!;
    // The same body the models page sends.
    expect(JSON.parse(init.body)).toEqual({ horizon: 7, max_epochs: 30, auto_promote: true });
    expect(init.headers["x-aurora-csrf"]).toBe("1");
    expect(init.headers["idempotency-key"]).toMatch(/[0-9a-f-]{36}/);
    expect(await within(trainCard()).findByText("queued")).toBeInTheDocument();
    expect(within(trainCard()).getByRole("button", { name: "Start training" })).toBeDisabled();
    expect(screen.getByRole("navigation", { name: "Upload wizard" }).querySelector('[aria-current="step"]')).toHaveTextContent("Train a model");
  });

  it("tells an operator why training is unavailable and never offers the form", async () => {
    asRole("operator");
    renderPage();
    await upload();
    expect(screen.queryByRole("button", { name: "Continue to training" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue to training" }));
    expect(within(trainCard()).getByText(/train-models permission, which the analyst and admin roles have/)).toBeInTheDocument();
    expect(within(trainCard()).getByText(/Your role \(operator\) cannot start training/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start training" })).toBeNull();
    expect(screen.getByText(/not available for your role/)).toBeInTheDocument();
    // No training request and no polling of training jobs for a role that cannot train.
    expect(fetchMock.mock.calls.some(([u]) => String(u).startsWith("/api/proxy/train") || String(u).startsWith("/api/proxy/jobs"))).toBe(false);
  });

  it("explains a viewer can neither upload nor train", () => {
    asRole("viewer");
    renderPage();
    expect(screen.getByText("Your role can view datasets but not upload them.")).toBeInTheDocument();
    expect(within(trainCard()).getByText(/Your role \(viewer\) cannot start training/)).toBeInTheDocument();
  });

  it("does not offer training for a rejected file", async () => {
    asRole("admin");
    uploaded = dataset({ status: "rejected", accepted_rows: 0, quality_report: { rejection_reason: "no valid rows" } });
    renderPage();
    const file = new File(["x"], "bad.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("File"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload and validate" }));
    expect(await screen.findByText(/no valid rows/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue to training" })).toBeNull();
    expect(within(trainCard()).getByText(/This file was rejected/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start training" })).toBeNull();
  });

  it("shows the API's refusal when training is already running", async () => {
    asRole("analyst");
    fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
      const u = new URL(url, "http://x");
      const method = init?.method ?? "GET";
      if (u.pathname === "/api/proxy/datasets") return json({ items: [], total: 0, limit: 10, offset: 0 });
      if (u.pathname === "/api/proxy/upload-dataset") return json({ dataset: uploaded, duplicate: false });
      if (u.pathname === "/api/proxy/jobs") return json({ items: [], total: 0, limit: 1, offset: 0 });
      if (u.pathname === "/api/proxy/train" && method === "POST") {
        return json({ error: { code: "conflict", message: "A training job is already queued or running for this organisation" } }, 409);
      }
      throw new Error(`unexpected ${method} ${url}`);
    });
    renderPage();
    await upload();
    fireEvent.click(screen.getByRole("button", { name: "Continue to training" }));
    fireEvent.click(await screen.findByRole("button", { name: "Start training" }));
    expect(await within(trainCard()).findByText(/already queued or running/)).toBeInTheDocument();
  });

  it("rejects an invalid epoch count before calling the API", async () => {
    asRole("analyst");
    renderPage();
    await upload();
    fireEvent.click(screen.getByRole("button", { name: "Continue to training" }));
    fireEvent.change(await screen.findByLabelText("Max epochs"), { target: { value: "500" } });
    fireEvent.click(screen.getByRole("button", { name: "Start training" }));
    expect(await screen.findByText("Epochs must be a whole number from 1 to 200")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([u]) => u === "/api/proxy/train")).toBe(false);
  });
});
