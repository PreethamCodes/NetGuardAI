import type { AlertItem, LiveSession, ModelsResponse, RunInfo, Timeline, MonitorRecord } from "./types";

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
  liveStreams: () => get<LiveSession[]>("/api/live/streams"),
  /** A stored live session, loaded as a playable timeline (records + alerts). */
  liveSession: async (id: string): Promise<Timeline> => {
    const q = encodeURIComponent(id);
    const recs = await get<{ records: MonitorRecord[] }>(`/api/live/records?stream_id=${q}&limit=2000`);
    const alerts = await get<Array<AlertItem & { id: number }>>(`/api/alerts?source=live&stream_id=${q}&limit=1000`);
    return {
      experiment_id: id, scenario: "live session", split: "live", repetition: 0, historical: false, seen_by_models: false,
      records: recs.records, alerts: [...alerts].reverse(),
    };
  },
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
