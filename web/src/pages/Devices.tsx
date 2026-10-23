import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Device, Pt, Surface } from "../api";
import { useLive } from "../live";
import { useFetch, useThrottled } from "../hooks";
import CornerPin from "../components/CornerPin";
import MaskEditor from "../components/MaskEditor";

const GUIDES: [string | null, string][] = [
  ["outline", "Outline"],
  ["crosshair", "Corners"],
  ["grid", "Grid"],
  ["colorbars", "Color bars"],
  ["gray", "Gray ramp"],
  ["white", "White"],
  ["black", "Black"],
  ["identify", "Identify"],
  [null, "Off"],
];

const DEFAULT_CORNERS: Pt[] = [
  [0, 0],
  [1, 0],
  [1, 1],
  [0, 1],
];

export default function DevicesPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { devices, toast } = useLive();
  const { data: surfaces = [] } = useFetch<Surface[]>("/api/surfaces", ["layout"], []);
  const players = devices.filter((d) => d.role === "player");
  const live = players.find((d) => d.id === id) ?? null;
  const [dev, setDev] = useState<Device | null>(null);
  const [guide, setGuide] = useState<string | null>(null);
  const { data: release, reload: reloadRelease } = useFetch<{ available: boolean; version_code?: number; version_name?: string; error?: string }>("/api/app-release");
  const [commandBusy, setCommandBusy] = useState(false);
  async function command(action: string) {
    if (!dev) return;
    setCommandBusy(true);
    try { await api.post(`/api/devices/${dev.id}/command`, { action }); toast("Command sent to player"); }
    catch (e) { toast(String(e), true); }
    finally { setCommandBusy(false); }
  }

  useEffect(() => {
    if (!id && players.length) nav(`/devices/${players[0].id}`, { replace: true });
  }, [id, players, nav]);

  // Only take server state when switching devices so live edits aren't overwritten.
  useEffect(() => {
    setDev(live);
    setGuide(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live?.id]);

  const save = useThrottled((devId: string, patch: Partial<Device>) => {
    api.patch(`/api/devices/${devId}`, patch).catch((e) => toast(e.message, true));
  }, 60);

  const update = (patch: Partial<Device>) => {
    if (!dev) return;
    setDev({ ...dev, ...patch });
    save(dev.id, patch);
  };

  const showGuide = async (pattern: string | null) => {
    if (!dev) return;
    try {
      await api.post(`/api/devices/${dev.id}/guide`, { pattern, params: pattern === "identify" ? { seconds: 5 } : {} });
      setGuide(pattern === "identify" ? null : pattern);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const surface = surfaces.find((s) => s.id === dev?.surface_id);
  const surfaceAspect = surface ? surface.width / surface.height : 2 / 3;

  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="row">
        <h1 style={{ margin: 0 }}>Devices</h1>
        <Link to="/setup" className="btn primary">+ Add a stick</Link>
        {players.map((d) => (
          <Link key={d.id} to={`/devices/${d.id}`} className="btn" style={d.id === id ? { borderColor: "var(--accent)" } : undefined}>
            <span className={`dot ${d.online ? "on" : ""}`} /> {d.name}
          </Link>
        ))}
      </div>
      {dev && <div className="panel small">H.264 720p60: {dev.capabilities.h264_720p60 === true ? "decoder reports support" : dev.capabilities.h264_720p60 === false ? "not supported" : "unknown — update player APK"} · Display: {String(dev.capabilities.display_refresh_hz ?? "unknown")} Hz · Cache budget: {String(dev.capabilities.cache_budget_mb ?? "unknown")} MB · Physical playback unverified</div>}
      {dev && <div className="panel col">
        <h3 style={{ margin: 0 }}>Player management</h3>
        <div className="small">Installed: {String(live?.capabilities.app_version ?? "unknown")} (build {String(live?.capabilities.version_code ?? "unknown")}) · Server release: {release?.available ? `${release.version_name} (build ${release.version_code})` : release?.error ?? "checking…"}</div>
        <div className="row">
          <button onClick={reloadRelease}>Check server release</button>
          <button disabled={!live?.online || live.capabilities.management_protocol !== 1 || commandBusy} onClick={() => command("restart")}>Restart player</button>
          <button disabled={!live?.online || live.capabilities.management_protocol !== 1 || !release?.available || Number(live.capabilities.version_code ?? 0) >= Number(release.version_code ?? 0) || commandBusy} onClick={() => command("update")}>Update app</button>
          <button disabled={!live?.online || live.capabilities.management_protocol !== 1 || commandBusy} onClick={() => command("boot_settings")}>Enable start after reboot</button>
          <button disabled={!live?.online || live.capabilities.management_protocol !== 1 || commandBusy} onClick={() => command("update_settings")}>Allow app updates</button>
          {live?.capabilities.maintenance_adb === true && <button disabled={!live.online || commandBusy} onClick={() => command("maintenance_adb")}>Reconnect maintenance ADB</button>}
          {live?.capabilities.tv_menu === true && <button disabled={!live.online || commandBusy} onClick={() => command("player_settings")}>Open player settings</button>}
          <button onClick={() => navigator.clipboard ? navigator.clipboard.writeText(`${location.origin}/devices/${dev.id}`).then(() => toast("Device link copied")).catch(() => toast("Select and copy the device link below")) : toast("Select and copy the device link below")}>Copy device link</button>
        </div>
        <label className="small">Device link<input readOnly value={`${location.origin}/devices/${dev.id}`} onFocus={(e) => e.currentTarget.select()} /></label>
        <div className="small muted">The player reconnects to this server automatically. Updates verify the APK checksum, package, version and signing certificate. Android may ask you to confirm installation on the stick. Restart player restarts this app, not Android.</div>
        {live?.capabilities.management_protocol !== 1 && <div>Install the latest APK once to enable these controls.</div>}
        <div className="small">Boot permission: {live?.status.management?.boot_permission ? "allowed" : "not confirmed"} · Install permission: {live?.status.management?.install_permission ? "allowed" : "not confirmed"}</div>
        {live?.status.management?.appliance_mode && <div className="small">Projection appliance · Home app: {live.status.management.default_home ? "Projector" : "not confirmed"} · {!live.online ? "Offline (last reported state below)" : live.status.management.playback_state === "playing" ? "Playing" : "Black, silent standby (LAN connected)"} · Loaded decoders: {live.status.management.loaded_decoders ?? "unknown"}. Standby keeps the stick powered; it does not turn off the projector.</div>}
        {live?.status.management && <div className="small">Playback telemetry since app start: {live.status.management.clip_loads ?? 0} clip loads · {live.status.management.buffer_events ?? 0} stalls ({live.status.management.buffer_ms ?? 0} ms) · {live.status.management.dropped_frames ?? 0} dropped frames · {live.status.management.playback_errors ?? 0} errors.</div>}
        <div role="status" className="small">{live?.status.management?.state ?? live?.status.command?.state ?? "Ready"}: {live?.status.management?.message ?? live?.status.command?.message ?? ""}</div>
      </div>}
      {!dev ? (
        <div className="panel muted">No players yet. Launch the app on a streaming stick and point it at this server.</div>
      ) : (
        <div className="split">
          <div className="col">
            <div className="panel col">
              <div className="row">
                <h3 style={{ margin: 0 }}>Corner pin</h3>
                <span className="muted small">Select a corner and nudge it until the outline sits exactly on the window. Dragging is optional.</span>
                <button className="small right" onClick={() => update({ corners: DEFAULT_CORNERS })}>
                  Reset
                </button>
              </div>
              <div className="row">
                {GUIDES.map(([p, label]) => (
                  <button key={label} className={`small ${guide === p && p ? "primary" : ""}`} onClick={() => showGuide(p)}>
                    {label}
                  </button>
                ))}
              </div>
              <CornerPin corners={dev.corners} aspect={dev.output_width / dev.output_height} onChange={(corners) => update({ corners })} />
            </div>
            <div className="panel col">
              <h3 style={{ margin: 0 }}>Mask</h3>
              <span className="muted small">Trace the visible glass if the window isn't a clean rectangle (arched tops, mullions).</span>
              <MaskEditor mask={dev.mask} aspect={surfaceAspect} onChange={(mask) => update({ mask })} />
              <label>
                Edge feather: {(dev.feather * 100).toFixed(1)}%
                <input type="range" min={0} max={0.1} step={0.002} value={dev.feather} onChange={(e) => update({ feather: Number(e.target.value) })} />
              </label>
            </div>
          </div>
          <div className="col">
            <div className="panel col">
              <label>
                Name
                <input value={dev.name} onChange={(e) => update({ name: e.target.value })} />
              </label>
              <label>
                Window
                <select
                  value={dev.surface_id ?? ""}
                  onChange={(e) => update(e.target.value ? { surface_id: e.target.value } : ({ surface_id: null, unassign: true } as Partial<Device>))}
                >
                  <option value="">unassigned</option>
                  {surfaces.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="row">
                <label className="inline">
                  <input type="checkbox" checked={dev.mirror_h} onChange={(e) => update({ mirror_h: e.target.checked })} /> Mirror horizontally
                </label>
                <label className="inline">
                  <input type="checkbox" checked={dev.mirror_v} onChange={(e) => update({ mirror_v: e.target.checked })} /> Mirror vertically
                </label>
              </div>
              <span className="small muted">
                Rear projection usually needs horizontal mirroring, unless the projector's own rear mode is on or the corners were set by camera calibration.
              </span>
            </div>
            <div className="panel col">
              <h3 style={{ margin: 0 }}>Color</h3>
              <Slider label="Brightness" value={dev.brightness} min={0} max={1.5} onChange={(v) => update({ brightness: v })} />
              <Slider label="Gamma" value={dev.gamma} min={0.5} max={2} onChange={(v) => update({ gamma: v })} />
              <Slider label="Red" value={dev.gain_r} min={0} max={1.5} onChange={(v) => update({ gain_r: v })} />
              <Slider label="Green" value={dev.gain_g} min={0} max={1.5} onChange={(v) => update({ gain_g: v })} />
              <Slider label="Blue" value={dev.gain_b} min={0} max={1.5} onChange={(v) => update({ gain_b: v })} />
              <button className="small" onClick={() => update({ brightness: 1, gamma: 1, gain_r: 1, gain_g: 1, gain_b: 1 })}>
                Reset color
              </button>
            </div>
            <div className="panel col">
              <h3 style={{ margin: 0 }}>Audio</h3>
              <label className="inline">
                <input type="checkbox" checked={dev.audio} onChange={(e) => update({ audio: e.target.checked })} /> Play audio on this projector
              </label>
              <Slider label="Volume" value={dev.volume} min={0} max={1} onChange={(v) => update({ volume: v })} />
            </div>
            <div className="panel col">
              <h3 style={{ margin: 0 }}>Local media cache</h3>
              <label>
                Maximum cached clips
                <select value={dev.cache_budget_mb} onChange={(e) => update({ cache_budget_mb: Number(e.target.value) })}>
                  <option value={768}>768 MB</option><option value={1024}>1 GB</option><option value={1536}>1.5 GB</option><option value={2048}>2 GB</option>
                </select>
              </label>
              <span className="small muted">Active-program clips are protected. Older clips are removed least-recently-used, while at least 300 MB remains free for Android.</span>
            </div>
            <div className="panel col small">
              <div>
                ID <span className="mono">{dev.id}</span>
              </div>
              <div className="muted">{String(dev.capabilities?.model ?? "")}</div>
              <div className="muted">
                Decoders: {String(dev.capabilities?.max_decoders ?? "?")} ({dev.capabilities?.dual_decoder ? "crossfades/wipes supported" : "single decoder: crossfades become dips"})
              </div>
              <Link to="/calibrate">Auto-calibrate with your phone camera</Link>
              <button
                className="danger small"
                onClick={async () => {
                  if (!confirm("Forget this device? It re-registers if it connects again.")) return;
                  await api.del(`/api/devices/${dev.id}`);
                  nav("/devices");
                }}
              >
                Forget device
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Slider({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (v: number) => void }) {
  return (
    <label>
      {label}: {value.toFixed(2)}
      <input type="range" min={min} max={max} step={0.01} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}
