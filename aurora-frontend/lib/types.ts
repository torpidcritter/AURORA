// Types mirroring the Aurora API responses (see aurora-backend/app/api/routes).

export type Role = "viewer" | "operator" | "analyst" | "admin";
export type RiskLevel = "critical" | "high" | "medium" | "low" | "unknown";
export type Severity = "low" | "medium" | "high" | "critical";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Me {
  id: string;
  org_id: string;
  org_name: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  permissions: string[];
  last_login_at: string | null;
  created_at: string;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

/** A one-time password reset link. Shown to the administrator once; only its hash is stored. */
export interface ResetLink {
  reset_url: string;
  expires_at: string;
}

export interface QualityReport {
  total_rows?: number;
  accepted_rows?: number;
  rejected_rows?: number;
  empty_rows?: number;
  duplicates_merged?: number;
  explicit_zero_rows?: number;
  gap_days?: number;
  expected_days?: number;
  products?: number;
  date_min?: string | null;
  date_max?: string | null;
  error_counts?: Record<string, number>;
  error_samples?: { row: number; code: string; message: string }[];
  warnings?: string[];
  quality_score?: number;
  quality_formula?: string;
  rejection_reason?: string;
  details?: unknown;
}

export interface Dataset {
  id: string;
  filename: string;
  file_format: string;
  size_bytes: number;
  status: "imported" | "rejected";
  tags: Record<string, string>;
  missing_policy: string;
  row_count: number;
  accepted_rows: number;
  rejected_rows: number;
  quality_score: number;
  quality_report: QualityReport;
  column_mapping: Record<string, string>;
  created_at: string;
}

export interface Product {
  id: string;
  sku: string;
  name: string;
  category: string | null;
  unit_cost: number | null;
  on_hand: number;
  on_order: number;
  inventory_known: boolean;
  lead_time_days: number | null;
  service_level: number | null;
  pack_size: number;
  min_order_qty: number;
  missing_policy: "zero" | "interpolate";
  version: number;
  first_date: string | null;
  last_date: string | null;
  history_days: number;
  units_30d: number;
  avg_daily_30d: number;
}

export interface HistoryPoint {
  date: string;
  units: number;
  imputed: boolean;
}

export interface ForecastPoint {
  date: string;
  yhat: number;
  lower: number | null;
  upper: number | null;
}

export interface MethodMetrics {
  mae: number | null;
  rmse: number | null;
  wape: number | null;
  mape: number | null;
  mape_excluded_zero_actuals: number;
  bias: number | null;
  n: number;
  per_horizon_mae?: (number | null)[];
}

export interface BacktestSummary {
  folds: number;
  origins: number;
  best_method: string | null;
  error?: string | null;
  methods: Record<string, MethodMetrics>;
}

export interface Forecast {
  product_id: string;
  sku: string;
  as_of: string;
  horizon: number;
  method: string;
  method_reason: string;
  transformer_status: string;
  model_version: string | null;
  points: ForecastPoint[];
  interval: {
    method: string;
    coverage: number;
    calibrated: boolean;
    empirical_coverage: number | null;
    n_calibration: number | null;
    n_test: number | null;
    note: string;
  };
  epistemic_uncertainty: { method: string; samples: number; std: number[]; note: string } | null;
  error_sd: number;
  error_sd_source: string;
  backtest: BacktestSummary | null;
  signals: Signal[];
  warnings: string[];
  forecast_id?: string;
}

export interface Recommendation {
  product_id: string;
  sku: string;
  name: string;
  status: "ok" | "unknown_inventory" | "no_forecast";
  risk_level: RiskLevel;
  message?: string;
  assumptions: string[];
  mean_daily_demand?: number;
  sigma_d?: number;
  z?: number;
  safety_stock?: number;
  reorder_point?: number;
  order_up_to?: number;
  inventory_position?: number;
  recommended_order_qty?: number;
  qty_to_order_up_to?: number;
  should_reorder?: boolean;
  days_until_stockout?: number | null;
  days_of_cover?: number | null;
  stockout_probability_lead_time?: number;
  urgency?: number;
  urgency_components?: { time: number; probability: number; inventory: number };
  overstock_units?: number;
  lead_time_days?: number;
  coverage_days?: number;
  service_level?: number;
  on_hand?: number;
  on_order?: number;
  forecast_method?: string;
  forecast_as_of?: string;
  sigma_source?: string;
  order_value?: number | null;
  overstock_value?: number | null;
  narrative?: string;
}

export interface Kpis {
  as_of: string | null;
  products_total: number;
  products_with_inventory: number;
  risk_counts: Record<RiskLevel, number>;
  inventory_health_score: number | null;
  units_last_30d: number;
  units_prev_30d: number;
  demand_change_pct: number | null;
  forecast_units_next_7d: number;
  reorder_now: number;
  reorder_value: number;
  overstock_units: number;
  overstock_value: number;
  open_alerts: number;
  critical_alerts: number;
  last_upload_at: string | null;
  active_model: { version: string; wape: number | null } | null;
  definitions: Record<string, string>;
}

export interface ActionItem {
  type: "reorder" | "overstock" | "data";
  priority: number;
  sku: string;
  product_id: string;
  risk_level?: RiskLevel;
  title: string;
  detail: string;
  quantity?: number;
  value?: number | null;
}

export interface Signal {
  id: string;
  name: string;
  signal_type?: string;
  type?: string;
  start_date: string;
  end_date: string;
  magnitude: number;
  product_id?: string | null;
}

export interface Job {
  id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  params: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  attempts: number;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at: string | null;
  epoch?: number | null;
  loss?: number | null;
  val_loss?: number | null;
  phase?: string | null;
}

export interface ModelVersion {
  id: string;
  version: string;
  kind: string;
  status: "candidate" | "active" | "archived" | "rejected";
  gate_passed: boolean;
  gate_reason: string | null;
  created_at: string;
  promoted_at: string | null;
  transformer_wape: number | null;
  best_baseline: string | null;
  best_baseline_wape: number | null;
  checksum: string;
  products: number;
  job_id: string | null;
}

export interface Importance {
  method: string;
  metric?: string;
  repeats?: number;
  baseline_mae?: number;
  features: { feature: string; delta_mae_mean: number; delta_mae_std: number }[];
}

export interface Diagnostics {
  model: ModelVersion;
  feature_importance: Importance | null;
  overall: Record<string, MethodMetrics | (number | null)[]>;
  interval_coverage: Record<string, number | null> | null;
  per_product: Record<
    string,
    { transformer: MethodMetrics; baselines_wape: Record<string, number | null>; best_baseline: string; transformer_beats_best_baseline: boolean }
  >;
  evaluation_segment: string;
  training: { epochs_run?: number; history?: { epoch: number; train_loss: number; val_loss: number }[]; duration_s?: number; skipped_products?: Record<string, string> };
}

export interface DriftRow {
  product_id: string;
  sku: string | null;
  status: string;
  drift: boolean;
  ks_statistic?: number;
  ks_pvalue?: number;
  mean_recent?: number;
  mean_baseline?: number;
  mean_change_ratio?: number | null;
  reasons?: string[];
  reason?: string;
}

export interface Alert {
  id: string;
  kind: string;
  severity: Severity;
  status: "open" | "acknowledged" | "resolved";
  title: string;
  message: string;
  product_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  resolved_at: string | null;
}

export interface AuditEvent {
  id: number;
  created_at: string;
  action: string;
  actor_email: string | null;
  resource_type: string | null;
  resource_id: string | null;
  outcome: "success" | "failure";
  ip: string | null;
  request_id: string | null;
  details: Record<string, unknown>;
}

export interface ScenarioResult {
  product_id: string;
  sku?: string;
  status: string;
  adjustments?: { type: string; params: Record<string, number>; signals_applied?: string[] }[];
  curve?: { date: string; baseline: number; scenario: number }[];
  baseline: Recommendation;
  scenario: Recommendation | null;
  deltas?: Record<string, number | null>;
}

export interface ScenarioTypeSpec {
  description: string;
  params: Record<string, { min: number; max: number; default: number }>;
}

export interface Explanation {
  product_id: string;
  sku: string;
  as_of: string;
  drivers: { driver: string; value: number | string; text: string; significant?: boolean; p_value?: number | null }[];
  model_feature_importance: Importance | null;
  method_note: string;
}

export interface ChatResponse {
  answer: string;
  mode: "claude" | "fallback";
  tool_calls: { tool: string; ok: boolean }[];
  notice?: string | null;
}
