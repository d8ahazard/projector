import { useRef, useState } from "react";
import { CompiledPreview, fmtDuration, MediaAsset, Slot, Surface } from "../api";

interface Props {
  preview: CompiledPreview | null;
  surfaces: Surface[];
  assets: MediaAsset[];
  selectedCue: string | null;
  onSelect: (cueId: string) => void;
  /** Dragging a slot sideways nudges that cue's start on that window (stored as an override). */
  onOffset: (cueId: string, surfaceId: string, deltaSeconds: number) => void;
}

const TRACK_H = 34;
const HEAD_W = 110;
const PALETTE = ["#ff7a1a", "#7ad7ff", "#a78bfa", "#4ade80", "#f472b6", "#facc15", "#fb7185", "#38bdf8"];

export default function Timeline({ preview, surfaces, assets, selectedCue, onSelect, onOffset }: Props) {
  const [pxPerSec, setPxPerSec] = useState(12);
  const [drag, setDrag] = useState<{ slot: Slot; startX: number; dx: number } | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  if (!preview) return <div className="muted">Compiling…</div>;
  const dur = Math.max(preview.duration_ms, 1000);
  const width = HEAD_W + (dur / 1000) * pxPerSec + 40;
  const height = 24 + surfaces.length * TRACK_H;
  const title = (id: string) => assets.find((a) => a.id === id)?.title ?? id;
  const tickEvery = pxPerSec > 30 ? 1 : pxPerSec > 8 ? 5 : pxPerSec > 2 ? 30 : 120;

  return (
    <div className="col">
      <div className="row small">
        <span className="muted">Drag a clip sideways to fine-tune when it starts on that window.</span>
        <label className="inline right">
          Zoom
          <input type="range" min={0.5} max={60} step={0.5} value={pxPerSec} onChange={(e) => setPxPerSec(Number(e.target.value))} />
        </label>
        <span className="mono">{fmtDuration(dur)}</span>
      </div>
      <div ref={scroller} style={{ overflowX: "auto", background: "#08080c", borderRadius: 8 }}>
        <svg
          width={width}
          height={height}
          style={{ display: "block", touchAction: "none" }}
          onPointerMove={(e) => drag && setDrag({ ...drag, dx: e.clientX - drag.startX })}
          onPointerUp={() => {
            if (drag && Math.abs(drag.dx) > 2) onOffset(drag.slot.cue_id, drag.slot.surface_id, drag.dx / pxPerSec);
            setDrag(null);
          }}
        >
          {Array.from({ length: Math.ceil(dur / 1000 / tickEvery) + 1 }).map((_, i) => {
            const x = HEAD_W + i * tickEvery * pxPerSec;
            return (
              <g key={i}>
                <line x1={x} x2={x} y1={18} y2={height} stroke="#1a1a24" />
                <text x={x + 2} y={12} fontSize={10} fill="#8d8aa0">
                  {fmtDuration(i * tickEvery * 1000)}
                </text>
              </g>
            );
          })}
          {preview.cues.map((c, i) => (
            <rect key={i} x={HEAD_W + (c.start / 1000) * pxPerSec} y={18} width={Math.max(1, ((c.end - c.start) / 1000) * pxPerSec)} height={3} fill={PALETTE[i % PALETTE.length]} opacity={0.6} />
          ))}
          {surfaces.map((s, row) => (
            <g key={s.id}>
              <rect x={0} y={24 + row * TRACK_H} width={width} height={TRACK_H - 2} fill={row % 2 ? "#0d0d13" : "#101018"} />
              <text x={8} y={24 + row * TRACK_H + TRACK_H / 2 + 4} fontSize={12} fill="#e8e6f0">
                {s.name}
              </text>
            </g>
          ))}
          {preview.slots.map((sl, i) => {
            const row = surfaces.findIndex((s) => s.id === sl.surface_id);
            if (row < 0) return null;
            const isDrag = drag?.slot === sl;
            const x = HEAD_W + (sl.start / 1000) * pxPerSec + (isDrag ? drag!.dx : 0);
            const w = Math.max(2, ((sl.end - sl.start) / 1000) * pxPerSec);
            const color = PALETTE[sl.cue_index % PALETTE.length];
            const sel = sl.cue_id === selectedCue;
            const tw = (sl.transition.duration / 1000) * pxPerSec;
            return (
              <g
                key={i}
                style={{ cursor: "grab" }}
                onPointerDown={(e) => {
                  (e.target as Element).setPointerCapture?.(e.pointerId);
                  setDrag({ slot: sl, startX: e.clientX, dx: 0 });
                  onSelect(sl.cue_id);
                }}
              >
                <rect x={x} y={24 + row * TRACK_H + 3} width={w} height={TRACK_H - 8} rx={4} fill={color} opacity={sel ? 0.95 : 0.55} stroke={sel ? "#fff" : "none"} />
                {sl.transition.type !== "cut" && tw > 2 && (
                  <path d={`M${x},${24 + row * TRACK_H + TRACK_H - 5} L${x + Math.min(tw, w)},${24 + row * TRACK_H + 3} L${x},${24 + row * TRACK_H + 3} Z`} fill="#000" opacity={0.35} />
                )}
                {w > 40 && (
                  <text x={x + 6} y={24 + row * TRACK_H + TRACK_H / 2 + 3} fontSize={10} fill="#000" pointerEvents="none">
                    {title(sl.asset_id).slice(0, Math.floor(w / 6))}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
