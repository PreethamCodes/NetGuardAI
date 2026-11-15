import type { ModelsResponse, RunInfo, Timeline, MonitorRecord } from "./types";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

export const api = {
  runs: () => get<RunInfo[]>("/api/runs"),
  timeline: (id: string) => get<Timeline>(`/api/runs/${encodeURIComponent(id)}/timeline`),
  models: () => get<ModelsResponse>("/api/models"),
  recommendations: () => get<Record<string, string>>("/api/recommendations"),
  liveRecords: () => get<{ records: MonitorRecord[]; note: string }>("/api/live/records?limit=300"),
};

/** WebSocket to the live feed; messages are pushed by the backend whenever a live sample is ingested. */
export function openLiveSocket(
  onRecord: (r: MonitorRecord) => void,
  onState: (connected: boolean) => void,
): () => void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/live`);
  ws.onopen = () => onState(true);
  ws.onclose = () => onState(false);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "record") onRecord(msg as MonitorRecord);
  };
  return () => ws.close();
}
