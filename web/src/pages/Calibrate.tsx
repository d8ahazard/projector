import { useRef, useState } from "react";
import { api, CalibrationProfile, Pt } from "../api";
import { useLive } from "../live";
import { useFetch, useThrottled } from "../hooks";
import CornerControls from "../components/CornerControls";

declare global {
  interface Window {
    /** Present inside the Android controller app's WebView. */
    ProjectorNative?: { capture: (url: string, fieldsJson: string, lockExposure: boolean, callback: string) => void };
    [cb: `__nativeCb_${string}`]: ((json: string) => void) | undefined;
  }
}

/** Native camera (locks focus/exposure) that uploads the photo itself and returns the server's JSON. */
function nativeCapture<T>(url: string, fields: Record<string, string>, lockExposure: boolean): Promise<T> {
  return new Promise((resolve, reject) => {
    const name = `__nativeCb_${Math.random().toString(36).slice(2)}` as const;
    window[name] = (json: string) => {
      delete window[name];
      try {
        const r = JSON.parse(json);
        if (r && r.__error) reject(new Error(r.__error));
        else resolve(r as T);
      } catch (e) {
        reject(e);
      }
    };
    window.ProjectorNative!.capture(url, JSON.stringify(fields), lockExposure, name);
  });
}

interface PhotoResult {
  ok: boolean;
  message: string;
  info: { markers_found: number; markers_total: number; reprojection_px?: number };
  photo_url: string;
  overlay_url: string;
  photo_size: [number, number];
  corners: Pt[] | null;
}

const DEFAULT_TAP: Pt[] = [
  [0.3, 0.3],
  [0.7, 0.3],
  [0.7, 0.7],
  [0.3, 0.7],
];

function PhotoInput({ label, onFile, disabled }: { label: string; onFile: (f: File) => void; disabled?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <button className="primary" disabled={disabled} onClick={() => ref.current?.click()}>
        {label}
      </button>
      <input
        ref={ref}
        type="file"
        accept="image/*"
        capture="environment"
        style={{ display: "none" }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
    </>
  );
}

function CornerOverlay({ src, corners, onChange, selected, onSelect }: { src: string; corners: Pt[]; onChange: (c: Pt[]) => void; selected: number; onSelect: (i: number) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<number | null>(null);
  const toNorm = (e: React.PointerEvent): Pt => {
    const r = box.current!.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  };
  return (
    <div
      ref={box}
      style={{ position: "relative", touchAction: "none", userSelect: "none" }}
      onPointerMove={(e) => {
        if (drag.current === null) return;
        const c = corners.slice();
        c[drag.current] = toNorm(e);
        onChange(c);
      }}
      onPointerUp={() => (drag.current = null)}
      onPointerLeave={() => (drag.current = null)}
    >
      <img src={src} style={{ width: "100%", display: "block", borderRadius: 6 }} draggable={false} />
      <svg viewBox="0 0 1 1" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}>
        <polygon points={corners.map((p) => p.join(",")).join(" ")} fill="rgba(255,122,26,0.15)" stroke="#ff7a1a" strokeWidth={0.004} />
      </svg>
      {corners.map((p, i) => (
        <div
          key={i}
          onPointerDown={(e) => {
            (e.target as HTMLElement).setPointerCapture(e.pointerId);
            drag.current = i;
            onSelect(i);
          }}
          style={{
            position: "absolute",
            left: `${p[0] * 100}%`,
            top: `${p[1] * 100}%`,
            width: 34,
            height: 34,
            marginLeft: -17,
            marginTop: -17,
            borderRadius: 17,
            border: "3px solid #ff7a1a",
            background: selected === i ? "#ff7a1a" : "rgba(0,0,0,0.35)",
            color: selected === i ? "#000" : "#fff",
            fontSize: 11,
            display: "grid",
            placeItems: "center",
            cursor: "grab",
          }}
        >
          {["TL", "TR", "BR", "BL"][i]}
        </div>
      ))}
    </div>
  );
}

function Geometry() {
  const { devices, toast } = useLive();
  const players = devices.filter((d) => d.role === "player");
  const [deviceId, setDeviceId] = useState("");
  const [sid, setSid] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PhotoResult | null>(null);
  const [corners, setCorners] = useState<Pt[]>(DEFAULT_TAP);
  const [showOverlay, setShowOverlay] = useState(false);
  const [livePreview, setLivePreview] = useState(true);
  const [selectedCorner, setSelectedCorner] = useState(0);
  const dev = deviceId || players.find((d) => d.online)?.id || "";
  const selectedDevice = players.find((d) => d.id === dev);
  const mountSideways = !!selectedDevice && Math.abs((selectedDevice.corners[1][1] - selectedDevice.corners[0][1]) * selectedDevice.output_height) > Math.abs((selectedDevice.corners[1][0] - selectedDevice.corners[0][0]) * selectedDevice.output_width);

  const analyze = async (sessionId: string, f?: File) => {
    setBusy(true);
    try {
      const url = `/api/calibration/${sessionId}/photo`;
      let r: PhotoResult;
      if (f) {
        const fd = new FormData();
        fd.append("photo", f);
        r = await api.post<PhotoResult>(url, fd);
      } else r = await nativeCapture<PhotoResult>(url, {}, false);
      setResult(r);
      setCorners(r.corners ?? DEFAULT_TAP);
      if (r.ok && r.corners) {
        await api.post(`/api/calibration/${sessionId}/apply`, { corners: r.corners });
        toast("Window found and applied. Drag a corner only if the outline needs a final nudge.");
      } else {
        toast(r.message, !r.ok);
      }
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const recalibrate = async () => {
    try {
      const r = await api.post<{ session: string }>("/api/calibration/geometry/start", { device_id: dev });
      setSid(r.session);
      setResult(null);
      if (window.ProjectorNative) await analyze(r.session);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const upload = async (f?: File) => {
    if (sid) await analyze(sid, f);
  };

  const apply = async () => {
    try {
      await api.post(`/api/calibration/${sid}/apply`, { corners });
      toast("Applied. The projector now shows an outline; it should trace the window edge.");
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const previewCorners = useThrottled((next: Pt[]) => {
    if (sid && result?.ok && livePreview) api.post(`/api/calibration/${sid}/apply`, { corners: next }).catch((e) => toast(e.message, true));
  }, 90);

  const adjustCorners = (next: Pt[]) => {
    setCorners(next);
    previewCorners(next);
  };

  const finish = async () => {
    if (sid) await api.post(`/api/calibration/${sid}/finish`).catch(() => undefined);
    setSid(null);
    setResult(null);
  };

  return (
    <div className="col">
      <div className="panel col">
        <div className="small muted">
          Choose a projector and tap Recalibrate. It shows the marker pattern, opens the phone camera, finds the window, and applies the fit. If needed,
          select any corner and nudge it with large arrow buttons while watching the outline move live.
        </div>
        <div className="row">
          <select value={dev} onChange={(e) => setDeviceId(e.target.value)} disabled={!!sid}>
            {players.map((d) => (
              <option key={d.id} value={d.id} disabled={!d.online}>
                {d.name} {d.online ? "" : "(offline)"}
              </option>
            ))}
          </select>
          {dev && <CalibrationProfiles deviceId={dev} />}
          {!sid ? (
            <button className="primary" disabled={!dev || busy} onClick={recalibrate}>
              Recalibrate
            </button>
          ) : (
            <>
              {window.ProjectorNative ? (
                <button className="primary" disabled={busy} onClick={() => upload()}>
                  Phone camera
                </button>
              ) : null}
              <PhotoInput label={busy ? "Analyzing…" : result ? "Retake photo" : "Take photo"} onFile={upload} disabled={busy} />
              <button onClick={finish}>Done</button>
            </>
          )}
        </div>
        {selectedDevice && <div className="small muted">Scene playback will automatically choose <b>{mountSideways ? "portrait / vertical" : "the assigned surface's orientation"}</b> variants for this calibration{mountSideways ? " because the output is side-mounted" : ""}.</div>}
      </div>
      {result && (
        <div className="panel col">
          <div className="row small">
            <span>
              Markers {result.info.markers_found}/{result.info.markers_total}
              {result.info.reprojection_px !== undefined && ` · error ${result.info.reprojection_px}px`}
            </span>
            <label className="inline right">
              <input type="checkbox" checked={showOverlay} onChange={(e) => setShowOverlay(e.target.checked)} /> show detection
            </label>
          </div>
          <CornerOverlay src={showOverlay ? result.overlay_url : result.photo_url} corners={corners} onChange={adjustCorners} selected={selectedCorner} onSelect={setSelectedCorner} />
          <CornerControls corners={corners} selected={selectedCorner} onSelect={setSelectedCorner} onChange={adjustCorners} />
          <div className="row">
            <span className="small muted">Select a corner and use the arrow pad or exact percentages. Dragging the photo handles is optional.</span>
            <label className="inline"><input type="checkbox" checked={livePreview} onChange={e => setLivePreview(e.target.checked)} />Live projector preview</label>
            <button className="primary right" disabled={!result.ok} onClick={apply}>
              Apply to projector
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function CalibrationProfiles({ deviceId }: { deviceId: string }) {
  const { toast } = useLive();
  const { data: profiles = [], reload } = useFetch<CalibrationProfile[]>(`/api/calibration/profiles?device_id=${encodeURIComponent(deviceId)}`, ["calibration_profiles"], []);
  const [name, setName] = useState("");
  const create = async () => {
    try {
      await api.post("/api/calibration/profiles", { device_id: deviceId, name: name.trim() });
      setName(""); reload(); toast("Calibration profile saved");
    } catch (e) { toast((e as Error).message, true); }
  };
  const activate = async (id: string) => {
    try { await api.post(`/api/calibration/profiles/${id}/activate`); reload(); toast("Profile applied live to the projector"); }
    catch (e) { toast((e as Error).message, true); }
  };
  const save = async (id: string) => {
    if (!confirm("Replace this profile with the projector's current calibration?")) return;
    try { await api.post(`/api/calibration/profiles/${id}/save`); reload(); toast("Profile updated"); }
    catch (e) { toast((e as Error).message, true); }
  };
  return <details className="profile-picker">
    <summary>{profiles.find(p => p.active)?.name ?? (profiles.length ? "Unsaved calibration" : "Save a setup")}</summary>
    <div className="col">
      <p className="small muted">Save placements separately, such as Wall test and Front window. Changing geometry marks the current setup unsaved until you create or update a profile.</p>
      {profiles.map(profile => <div className="row" key={profile.id}><span className="grow">{profile.active ? "● " : "○ "}{profile.name}</span><button className="small" disabled={profile.active} onClick={() => activate(profile.id)}>Use</button><button className="small" onClick={() => save(profile.id)}>Update</button></div>)}
      <div className="row"><input className="grow" value={name} maxLength={80} placeholder="Wall test or Front window" onChange={e => setName(e.target.value)} /><button className="small primary" disabled={!name.trim()} onClick={create}>Save current as new</button></div>
    </div>
  </details>;
}

interface ColorResult {
  gain_r: number;
  gain_g: number;
  gain_b: number;
  gamma: number;
  measured_gamma: number;
}

function Color() {
  const { devices, toast } = useLive();
  const players = devices.filter((d) => d.role === "player" && d.online);
  const [picked, setPicked] = useState<string[]>([]);
  const [sid, setSid] = useState<string | null>(null);
  const [current, setCurrent] = useState<string | null>(null);
  const [done, setDone] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Record<string, ColorResult> | null>(null);
  const ids = picked.length ? picked : players.map((d) => d.id);
  const name = (id: string) => devices.find((d) => d.id === id)?.name ?? id;

  const start = async () => {
    try {
      const r = await api.post<{ session: string; current: string }>("/api/calibration/color/start", { device_ids: ids });
      setSid(r.session);
      setCurrent(r.current);
      setDone([]);
      setResult(null);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const upload = async (f?: File) => {
    if (!sid || !current) return;
    setBusy(true);
    try {
      type R = { ok: boolean; message?: string; next: string | null };
      const url = `/api/calibration/color/${sid}/photo`;
      let r: R;
      if (f) {
        const fd = new FormData();
        fd.append("device_id", current);
        fd.append("photo", f);
        r = await api.post<R>(url, fd);
      } else r = await nativeCapture<R>(url, { device_id: current }, true);
      if (!r.ok) toast(r.message ?? "Failed", true);
      else {
        setDone((d) => [...d, current]);
        setCurrent(r.next);
      }
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const solveIt = async () => {
    try {
      const r = await api.post<{ result: Record<string, ColorResult> }>(`/api/calibration/color/${sid}/solve`);
      setResult(r.result);
      toast("Color matched. Projectors show color bars for comparison.");
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const finish = async () => {
    if (sid) await api.post(`/api/calibration/color/${sid}/finish`).catch(() => undefined);
    setSid(null);
    setCurrent(null);
  };

  return (
    <div className="panel col">
      <div className="small muted">
        Each projector shows a gray ramp and color patches in turn. Use the same spot and zoom for every photo, and lock exposure if your camera allows it (the
        phone app does this for you). Brighter projectors are dimmed to match the dimmest one, and gamma is evened out.
      </div>
      {!sid ? (
        <div className="row">
          {players.map((d) => (
            <label key={d.id} className="inline">
              <input
                type="checkbox"
                checked={ids.includes(d.id)}
                onChange={(e) => setPicked(e.target.checked ? [...ids, d.id] : ids.filter((x) => x !== d.id))}
              />
              {d.name}
            </label>
          ))}
          <button className="primary right" disabled={ids.length === 0} onClick={start}>
            Start color match
          </button>
        </div>
      ) : (
        <>
          <div className="row">
            {ids.map((id) => (
              <span key={id} className="tag" style={id === current ? { borderColor: "var(--accent)" } : undefined}>
                {done.includes(id) ? "✓ " : id === current ? "▶ " : ""}
                {name(id)}
              </span>
            ))}
          </div>
          <div className="row">
            {current ? (
              <>
                <span>
                  Photograph <b>{name(current)}</b>
                </span>
                {window.ProjectorNative && (
                  <button className="primary" disabled={busy} onClick={() => upload()}>
                    Phone camera
                  </button>
                )}
                <PhotoInput label={busy ? "Measuring…" : "Take photo"} onFile={upload} disabled={busy} />
              </>
            ) : (
              <button className="primary" onClick={solveIt}>
                Solve and apply
              </button>
            )}
            <button className="right" onClick={finish}>
              Done
            </button>
          </div>
        </>
      )}
      {result && (
        <table>
          <thead>
            <tr>
              <th>Projector</th>
              <th>Gain R</th>
              <th>Gain G</th>
              <th>Gain B</th>
              <th>Measured gamma</th>
              <th>Correction</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(result).map(([id, r]) => (
              <tr key={id}>
                <td>{name(id)}</td>
                <td>{r.gain_r}</td>
                <td>{r.gain_g}</td>
                <td>{r.gain_b}</td>
                <td>{r.measured_gamma}</td>
                <td>{r.gamma}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function CalibratePage() {
  const [tab, setTab] = useState<"geometry" | "color">("geometry");
  return (
    <div className="col" style={{ gap: 16, maxWidth: 900 }}>
      <div className="page-heading"><div><span className="eyebrow">THE PERFECT FIT</span><h1>Made for your windows.</h1><p>Line up the edges. Match the color. Bring it all together.</p></div></div>
      <div className="panel col"><div className="row"><span className={`status-pill ${window.ProjectorNative ? "online" : ""}`}>{window.ProjectorNative ? "Phone camera available" : "Photo upload available"}</span><span className="small muted">Set up each projector in its final position first.</span></div><div className="steps"><div className="step"><span>1</span>Show the pattern</div><div className="step"><span>2</span>Frame & capture</div><div className="step"><span>3</span>Refine the edges</div></div></div>
      <div className="tabs">
        <button className={tab === "geometry" ? "active" : ""} onClick={() => setTab("geometry")}>
          Fit to window
        </button>
        <button className={tab === "color" ? "active" : ""} onClick={() => setTab("color")}>
          Color match
        </button>
      </div>
      {tab === "geometry" ? <Geometry /> : <Color />}
    </div>
  );
}
