// Shapes returned by the NetGuard AI backend (netguard/backend). Nothing here is invented client-side.

export interface RunInfo {
  experiment_id: string;
  scenario: string;
  scenario_label: string;
  repetition: number;
  split: "train" | "val" | "test";
  seconds: number;
  seen_by_models: boolean;
  historical: boolean;
}

export type Metrics = Record<string, number | null>;

export interface Unavailable {
  available: false;
  reason: string;
}

export interface Available {
  available: true;
  classification: { label: string; probabilities: Record<string, number> };
  anomaly: {
    score: number;
    threshold: number;
    flagged: boolean;
    kind: string | null;
    severity: "low" | "medium" | "high" | null;
    reasons: string[];
    note: string;
  };
  congestion: {
    probability: number;
    threshold: number;
    warning: boolean;
    horizon_s: number;
    window: [number, number];
    currently_congested: boolean;
  };
}

export type Prediction = Available | Unavailable;

export interface GroundTruth {
  scenario_label?: string;
  current_congestion?: boolean | null;
  future_congestion?: boolean | null;
  time_to_congestion?: number | null;
  future_label_available?: boolean | null;
}

export interface MonitorRecord {
  source: "replay" | "live";
  historical: boolean;
  second: number;
  timestamp_s: number | null;
  metrics: Metrics;
  predictions: Prediction;
  ground_truth?: GroundTruth;
  experiment_id?: string;
  stream_id?: string;
}

export interface AlertItem {
  id?: number;
  second: number;
  timestamp_s: number | null;
  kind: "anomaly" | "congestion";
  severity: "low" | "medium" | "high";
  title: string;
  explanation: string[];
}

export interface Timeline {
  experiment_id: string;
  scenario: string;
  split: string;
  repetition: number;
  historical: boolean;
  seen_by_models: boolean;
  records: MonitorRecord[];
  alerts: AlertItem[];
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

export interface ModelsResponse {
  in_use: {
    classifier: string;
    anomaly: string;
    congestion: string;
    classes: string[];
    feature_count: number;
    anomaly_threshold: number;
    congestion_threshold: number;
  };
  evaluation: Json;
  note: string;
}
