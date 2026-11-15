import type { components } from "@crew-health/contracts";
import type { CrewApi } from "../../shell/api/client";

export type LotsView = components["schemas"]["medicine-lots-view.schema"];
export type LotRow = LotsView["lots"][number];
export type LotState = "in_specification" | "adjust" | "do_not_use";

/* The fields this screen reads of the engine's LotAssessment (contracts/pharmacy/inventory-assessment.schema.json
   and potency-estimate.schema.json); listMedicineLotEstimates passes it on as the engine stored it. */
export interface Coverage {
  probability: number;
  low: number;
  high: number;
}
export interface Pathway {
  pathway_id: string;
  reaction_class_id: string;
  driver: "thermal" | "light" | "radiation";
  role: string;
  share_nominal: number;
  contributes: boolean;
  layer: string;
}
export interface TraceEntry {
  quantity: string;
  value: number | string | null;
  unit: string;
  origin: string;
  source_ids: string[];
  note: string;
}
export interface Placard {
  kind: string;
  message: string;
}
export interface Degradant {
  degradant_id: string;
  name: string;
  pathway_id: string;
  mean: number;
  limit_percent: number | null;
  p_above_limit: number | null;
  toxicity_flag: string;
}
export interface Radiation {
  cumulative_absorbed_dose_gy: number;
  included_in_potency: boolean;
  central_loss_fraction_median: number;
  upper_bound_loss_fraction: number;
  upper_bound_k_per_gy: number;
  method: string;
  radiosensitive: boolean;
  radiosensitive_threshold_gy: number;
  threshold_exceeded: boolean;
}
export interface Exposure {
  duration_s: number;
  mkt_k: number | null;
  mean_temperature_k: number | null;
  min_temperature_k: number | null;
  max_temperature_k: number | null;
  cumulative_absorbed_dose_gy: number;
  longest_gap_s: number;
}
export interface Estimate {
  estimate_id: string;
  ingredient_id: string;
  mean: number;
  standard_uncertainty: number;
  coverage_interval: Coverage;
  specification: { lower_percent: number; upper_percent: number };
  p_within_spec: number;
  p_below_lower: number;
  degradants: Degradant[];
  radiation: Radiation;
  exposure: Exposure;
  flags: string[];
  estimate_basis: string;
  pathway_contributions: Pathway[];
  placards: Placard[];
  monte_carlo: { n_draws: number; converged: boolean };
  trace: TraceEntry[];
}
export interface Reason {
  code: string;
  severity: "blocking" | "warning" | "info";
  ingredient_id: string | null;
  message: string;
}
export interface LotAssessment {
  lot_id: string;
  estimates: Estimate[];
  decision: { state: LotState; reasons: Reason[] };
}
export interface EstimatesView {
  environment_id: string;
  run_id: string | null;
  as_of: string | null;
  engine_version: string | null;
  lots: LotAssessment[];
}

export function loadLots(api: CrewApi): Promise<LotsView> {
  return api.get<LotsView>("/v1/medicines/lots");
}

export function loadEstimates(api: CrewApi): Promise<EstimatesView> {
  return api.get<EstimatesView>("/v1/medicines/lots/estimates");
}

/** The mark of a lot's state: lime in specification, a dashed ring to adjust, ink when it must not be used. */
export const STATE_MK: Record<LotState, string> = { in_specification: "meas", adjust: "gap", do_not_use: "stop" };

export function percent(value: number, digits = 1): string {
  return value.toFixed(digits);
}

/** A probability as a percentage, with one decimal below 10 % so small risks do not read as zero. */
export function chance(value: number): string {
  const pct = value * 100;
  if (pct > 0 && pct < 0.1) {
    return "<0.1";
  }
  return pct < 10 ? pct.toFixed(1) : pct.toFixed(0);
}

/** A snake_case engine identifier as words, for ids the engine has no display name for. */
export function words(id: string): string {
  const text = id.replace(/[_.:-]+/g, " ").trim();
  return text === "" ? id : (text[0] ?? "").toUpperCase() + text.slice(1);
}

export function traceValue(entry: TraceEntry): string {
  if (entry.value === null) {
    return "";
  }
  if (typeof entry.value === "string") {
    return entry.value;
  }
  const value = entry.value;
  const magnitude = Math.abs(value);
  const text = magnitude !== 0 && (magnitude >= 1e5 || magnitude < 1e-3) ? value.toExponential(3) : String(Number(value.toPrecision(4)));
  return entry.unit === "" || entry.unit === "1" ? text : `${text} ${entry.unit}`;
}
