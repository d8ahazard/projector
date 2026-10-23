import { useEffect, useState } from "react";
import { api, Cue, MarathonScheduler } from "../api";
import { useLive } from "../live";

type Previewable = Cue | MarathonScheduler;

export default function ProjectorPreview({ assetId, cue, label = "Play preview" }: { assetId?: string; cue?: Previewable; label?: string }) {
  const { devices, toast } = useLive();
  const players = devices.filter(d => d.role === "player" && d.online && d.surface_id);
  const [deviceId, setDeviceId] = useState(() => localStorage.getItem("projector.previewTarget") || "");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!players.some(p => p.id === deviceId)) setDeviceId(players[0]?.id || "");
  }, [players.map(p => `${p.id}:${p.online}`).join("|"), deviceId]); // eslint-disable-line react-hooks/exhaustive-deps

  const play = async () => {
    if (!deviceId) return;
    setBusy(true);
    localStorage.setItem("projector.previewTarget", deviceId);
    try {
      const target = players.find(p => p.id === deviceId);
      await api.post("/api/runner/preview", { device_ids: [deviceId], ...(cue ? { cue } : { asset_id: assetId }) });
      toast(`Preview queued on ${target?.name || "projector"}; it returns automatically afterward`);
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  return <div className="row projector-preview" style={{ flexWrap: "wrap" }}>
    <select aria-label="Preview projector" value={deviceId} onChange={e => setDeviceId(e.target.value)}>
      {!players.length && <option value="">No online assigned projectors</option>}
      {players.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
    </select>
    <button className="primary" disabled={busy || !deviceId || (!assetId && !cue)} onClick={play}>
      {busy ? "Caching preview…" : `▶ ${label}`}
    </button>
  </div>;
}
