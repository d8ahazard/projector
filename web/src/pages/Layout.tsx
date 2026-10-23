import { useEffect, useRef, useState } from "react";
import { api, Surface } from "../api";
import { useLive } from "../live";
import { useFetch, useThrottled } from "../hooks";

const GRID = 1; // inches

export default function LayoutPage() {
  const { devices, toast } = useLive();
  const { data: fetched = [], reload } = useFetch<Surface[]>("/api/surfaces", ["layout"], []);
  const [surfaces, setSurfaces] = useState<Surface[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ id: string; mode: "move" | "resize"; sx: number; sy: number; orig: Surface } | null>(null);

  useEffect(() => setSurfaces(fetched), [fetched]);

  const save = useThrottled((id: string, patch: Partial<Surface>) => {
    api.patch(`/api/surfaces/${id}`, patch).catch((e) => toast(e.message, true));
  }, 150);

  const update = (id: string, patch: Partial<Surface>) => {
    setSurfaces((ss) => ss.map((s) => (s.id === id ? { ...s, ...patch } : s)));
    save(id, patch);
  };

  const add = async () => {
    const s = await api.post<Surface>("/api/surfaces", {});
    reload();
    setSel(s.id);
  };

  const remove = async (id: string) => {
    if (!confirm("Delete this surface?")) return;
    await api.del(`/api/surfaces/${id}`);
    setSel(null);
    reload();
  };

  const assign = async (deviceId: string, surfaceId: string) => {
    await api.patch(`/api/devices/${deviceId}`, surfaceId ? { surface_id: surfaceId } : { unassign: true });
  };

  const x0 = Math.min(0, ...surfaces.map((s) => s.x)) - 24;
  const y0 = Math.min(0, ...surfaces.map((s) => s.y)) - 24;
  const x1 = Math.max(120, ...surfaces.map((s) => s.x + s.width)) + 24;
  const y1 = Math.max(80, ...surfaces.map((s) => s.y + s.height)) + 36;

  const toWorld = (e: React.PointerEvent) => {
    const pt = svg.current!.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const p = pt.matrixTransform(svg.current!.getScreenCTM()!.inverse());
    return { x: p.x, y: p.y };
  };

  const onDown = (e: React.PointerEvent, s: Surface, mode: "move" | "resize") => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture(e.pointerId);
    const p = toWorld(e);
    drag.current = { id: s.id, mode, sx: p.x, sy: p.y, orig: { ...s } };
    setSel(s.id);
  };

  const onMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const p = toWorld(e);
    const dx = Math.round((p.x - d.sx) / GRID) * GRID;
    const dy = Math.round((p.y - d.sy) / GRID) * GRID;
    if (d.mode === "move") update(d.id, { x: d.orig.x + dx, y: d.orig.y + dy });
    else update(d.id, { width: Math.max(6, d.orig.width + dx), height: Math.max(6, d.orig.height + dy) });
  };

  const sorted = [...surfaces].sort((a, b) => a.x - b.x);
  const gaps = sorted.slice(1).map((s, i) => {
    const prev = sorted[i];
    const gap = s.x - (prev.x + prev.width);
    return { x: prev.x + prev.width, y: Math.min(prev.y + prev.height, s.y + s.height) + 10, gap, w: gap };
  });
  const selected = surfaces.find((s) => s.id === sel);

  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="row">
        <h1 style={{ margin: 0 }}>Layout</h1>
        <span className="muted">Place each window to scale (inches). Drag to move, drag the corner to resize.</span>
        <button className="primary right" onClick={add}>
          Add window
        </button>
      </div>
      <div className="split">
        <div className="panel">
          <svg
            ref={svg}
            viewBox={`${x0} ${y0} ${x1 - x0} ${y1 - y0}`}
            style={{ width: "100%", maxHeight: "65vh", background: "#08080c", borderRadius: 8, touchAction: "none" }}
            onPointerMove={onMove}
            onPointerUp={() => (drag.current = null)}
            onPointerDown={() => setSel(null)}
          >
            <defs>
              <pattern id="grid12" width="12" height="12" patternUnits="userSpaceOnUse">
                <path d="M 12 0 L 0 0 0 12" fill="none" stroke="#1a1a24" strokeWidth="0.4" />
              </pattern>
            </defs>
            <rect x={x0} y={y0} width={x1 - x0} height={y1 - y0} fill="url(#grid12)" />
            {surfaces.map((s) => {
              const dev = devices.filter((d) => d.surface_id === s.id);
              return (
                <g key={s.id}>
                  <rect
                    x={s.x}
                    y={s.y}
                    width={s.width}
                    height={s.height}
                    fill={s.id === sel ? "#2a1a10" : "#15151d"}
                    stroke={s.id === sel ? "#ff7a1a" : "#3a3a4a"}
                    strokeWidth={0.8}
                    style={{ cursor: "move" }}
                    onPointerDown={(e) => onDown(e, s, "move")}
                  />
                  <text x={s.x + s.width / 2} y={s.y + s.height / 2} fill="#e8e6f0" fontSize={Math.min(6, s.width / 6)} textAnchor="middle" pointerEvents="none">
                    {s.name}
                  </text>
                  <text x={s.x + s.width / 2} y={s.y + s.height / 2 + 6} fill="#8d8aa0" fontSize={3.5} textAnchor="middle" pointerEvents="none">
                    {s.width}" × {s.height}" {dev.length ? `· ${dev.map((d) => d.name).join(", ")}` : "· no player"}
                  </text>
                  <rect
                    x={s.x + s.width - 3}
                    y={s.y + s.height - 3}
                    width={6}
                    height={6}
                    fill="#ff7a1a"
                    style={{ cursor: "nwse-resize" }}
                    onPointerDown={(e) => onDown(e, s, "resize")}
                  />
                </g>
              );
            })}
            {gaps.map((g, i) =>
              g.gap > 0 ? (
                <g key={i} pointerEvents="none">
                  <line x1={g.x} x2={g.x + g.w} y1={g.y} y2={g.y} stroke="#7ad7ff" strokeWidth={0.4} />
                  <text x={g.x + g.w / 2} y={g.y + 5} fill="#7ad7ff" fontSize={3.5} textAnchor="middle">
                    {g.gap}" ({(g.gap / 12).toFixed(1)} ft)
                  </text>
                </g>
              ) : null,
            )}
          </svg>
        </div>
        <div className="col">
          {selected ? (
            <div className="panel col">
              <h3 style={{ marginTop: 0 }}>Window</h3>
              <label>
                Name
                <input value={selected.name} onChange={(e) => update(selected.id, { name: e.target.value })} />
              </label>
              <div className="field-row">
                {(["x", "y", "width", "height"] as const).map((k) => (
                  <label key={k}>
                    {k} (in)
                    <input type="number" value={selected[k]} onChange={(e) => update(selected.id, { [k]: Number(e.target.value) })} />
                  </label>
                ))}
              </div>
              <label>
                Groups (comma separated, e.g. left, upstairs)
                <input
                  defaultValue={selected.groups.join(", ")}
                  key={selected.id}
                  onBlur={(e) =>
                    update(selected.id, {
                      groups: e.target.value
                        .split(",")
                        .map((g) => g.trim().toLowerCase())
                        .filter(Boolean),
                    })
                  }
                />
              </label>
              <label>
                Order (for "list" targeting)
                <input type="number" value={selected.sort} onChange={(e) => update(selected.id, { sort: Number(e.target.value) })} />
              </label>
              <button className="danger" onClick={() => remove(selected.id)}>
                Delete window
              </button>
            </div>
          ) : (
            <div className="panel muted">Select a window to edit its size, position, and groups.</div>
          )}
          <div className="panel col">
            <h3 style={{ marginTop: 0 }}>Players</h3>
            {devices.filter((d) => d.role === "player").length === 0 && <span className="muted">No players have connected yet.</span>}
            {devices
              .filter((d) => d.role === "player")
              .map((d) => (
                <div key={d.id} className="row">
                  <span className={`dot ${d.online ? "on" : ""}`} />
                  <span className="grow">{d.name}</span>
                  <select value={d.surface_id ?? ""} onChange={(e) => assign(d.id, e.target.value)}>
                    <option value="">unassigned</option>
                    {surfaces.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                  <button className="small" onClick={() => api.post(`/api/devices/${d.id}/identify`).catch((e) => toast(e.message, true))}>
                    Identify
                  </button>
                </div>
              ))}
          </div>
        </div>
      </div>
    </div>
  );
}
