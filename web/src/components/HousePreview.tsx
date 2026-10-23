import { useEffect, useMemo, useRef, useState } from "react";
import { CompiledPreview, fileUrl, fmtDuration, Keyframe, MediaAsset, Slot, Surface } from "../api";
import { useFetch } from "../hooks";

interface Props {
  programId?: string;
  preview?: CompiledPreview | null;
  surfaces: Surface[];
  assets: MediaAsset[];
  /** When set, follows the live runner clock instead of showing transport controls. */
  liveStart?: number;
  height?: number;
}

const ease = (p: number) => {
  const x = Math.min(1, Math.max(0, p));
  return x < 0.5 ? 2 * x * x : 1 - 2 * (1 - x) * (1 - x);
};

function rectAt(rect: Keyframe[], el: number): Keyframe {
  if (!rect.length) return { t: 0, x: 0, y: 0, w: 1, h: 1, opacity: 1 };
  if (rect.length === 1 || el <= rect[0].t) return rect[0];
  const last = rect[rect.length - 1];
  if (el >= last.t) return last;
  for (let i = 0; i < rect.length - 1; i++) {
    const a = rect[i];
    const b = rect[i + 1];
    if (el >= a.t && el <= b.t) {
      const f = b.t === a.t ? 1 : (el - a.t) / (b.t - a.t);
      const lerp = (u: number, v: number) => u + (v - u) * f;
      return { t: el, x: lerp(a.x, b.x), y: lerp(a.y, b.y), w: lerp(a.w, b.w), h: lerp(a.h, b.h), opacity: lerp(a.opacity ?? 1, b.opacity ?? 1) };
    }
  }
  return last;
}

function slotAlpha(s: Slot, t: number, next: Slot | undefined): { alpha: number; wipe: number | null } {
  const el = t - s.start;
  const d = Math.max(1, s.transition.duration);
  let inA = 1;
  let wipe: number | null = null;
  if (s.transition.type === "dip") inA = ease(el / (d / 2));
  else if (s.transition.type === "crossfade") inA = ease(el / d);
  else if (s.transition.type === "wipe" && el < d) wipe = ease(el / d);
  let outA = Math.min(1, Math.max(0, (s.end - t) / 300));
  if (next && next.start <= s.end + 50) {
    outA = next.transition.type === "dip" ? Math.min(1, Math.max(0, (next.start - t) / (Math.max(1, next.transition.duration) / 2))) : 1;
  }
  return { alpha: Math.min(inA, outA), wipe };
}

export default function HousePreview({ programId, preview, surfaces, assets, liveStart, height = 260 }: Props) {
  const { data: fetched } = useFetch<CompiledPreview>(programId && !preview ? `/api/programs/${programId}/preview` : null, ["programs", "layout", "media"]);
  const comp = preview ?? fetched ?? null;
  const canvas = useRef<HTMLCanvasElement>(null);
  const [playing, setPlaying] = useState(true);
  const [t, setT] = useState(0);
  const tRef = useRef(0);
  const images = useRef(new Map<string, HTMLImageElement>());
  const assetMap = useMemo(() => new Map(assets.map((a) => [a.id, a])), [assets]);
  const duration = comp?.duration_ms ?? 0;

  useEffect(() => {
    tRef.current = 0;
    setT(0);
  }, [comp]);

  // Slots per surface sorted by start, for next-slot lookups.
  const bySurface = useMemo(() => {
    const m = new Map<string, Slot[]>();
    for (const s of comp?.slots ?? []) {
      if (!m.has(s.surface_id)) m.set(s.surface_id, []);
      m.get(s.surface_id)!.push(s);
    }
    m.forEach((list) => list.sort((a, b) => a.start - b.start));
    return m;
  }, [comp]);

  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    const loop = (ts: number) => {
      const dt = ts - last;
      last = ts;
      if (liveStart && duration > 0) {
        tRef.current = (((Date.now() - liveStart) % duration) + duration) % duration;
      } else if (playing && duration > 0) {
        tRef.current = (tRef.current + dt) % duration;
      }
      draw(tRef.current);
      if (Math.floor(ts / 100) !== Math.floor((ts - dt) / 100)) setT(tRef.current);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [comp, playing, liveStart, surfaces, assetMap]);

  function image(assetId: string): HTMLImageElement | null {
    const a = assetMap.get(assetId);
    if (!a?.thumb_path) return null;
    let img = images.current.get(assetId);
    if (!img) {
      img = new Image();
      img.src = fileUrl(a.thumb_path);
      images.current.set(assetId, img);
    }
    return img.complete && img.naturalWidth ? img : null;
  }

  function draw(time: number) {
    const cv = canvas.current;
    if (!cv) return;
    const ctx = cv.getContext("2d")!;
    const W = cv.clientWidth;
    const H = height;
    if (cv.width !== W * devicePixelRatio) {
      cv.width = W * devicePixelRatio;
      cv.height = H * devicePixelRatio;
    }
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    ctx.fillStyle = "#08080c";
    ctx.fillRect(0, 0, W, H);
    if (!surfaces.length) {
      ctx.fillStyle = "#8d8aa0";
      ctx.fillText("Add surfaces on the Layout page", 12, 20);
      return;
    }
    const x0 = Math.min(...surfaces.map((s) => s.x));
    const y0 = Math.min(...surfaces.map((s) => s.y));
    const x1 = Math.max(...surfaces.map((s) => s.x + s.width));
    const y1 = Math.max(...surfaces.map((s) => s.y + s.height));
    const pad = 16;
    const scale = Math.min((W - pad * 2) / (x1 - x0), (H - pad * 2 - 14) / (y1 - y0));
    const ox = (W - (x1 - x0) * scale) / 2 - x0 * scale;
    const oy = pad - y0 * scale;

    for (const s of surfaces) {
      const sx = ox + s.x * scale;
      const sy = oy + s.y * scale;
      const sw = s.width * scale;
      const sh = s.height * scale;
      ctx.fillStyle = "#000";
      ctx.fillRect(sx, sy, sw, sh);
      const list = bySurface.get(s.id) ?? [];
      ctx.save();
      ctx.beginPath();
      ctx.rect(sx, sy, sw, sh);
      ctx.clip();
      list.forEach((slot, i) => {
        if (time < slot.start || time >= slot.end) return;
        const { alpha, wipe } = slotAlpha(slot, time, list[i + 1]);
        const r = rectAt(slot.rect, time - slot.start);
        const a = alpha * (r.opacity ?? 1);
        if (a <= 0) return;
        ctx.save();
        if (wipe != null) {
          const dir = slot.transition.direction ?? "right";
          ctx.beginPath();
          if (dir === "left") ctx.rect(sx + sw * (1 - wipe), sy, sw * wipe, sh);
          else if (dir === "up") ctx.rect(sx, sy + sh * (1 - wipe), sw, sh * wipe);
          else if (dir === "down") ctx.rect(sx, sy, sw, sh * wipe);
          else ctx.rect(sx, sy, sw * wipe, sh);
          ctx.clip();
        }
        ctx.globalAlpha = a;
        const img = image(slot.asset_id);
        const vx = sx + r.x * sw;
        const vy = sy + r.y * sh;
        if (img) ctx.drawImage(img, vx, vy, r.w * sw, r.h * sh);
        else {
          ctx.fillStyle = "#ff7a1a";
          ctx.fillRect(vx, vy, r.w * sw, r.h * sh);
        }
        ctx.restore();
      });
      ctx.restore();
      ctx.strokeStyle = "#2a2a38";
      ctx.lineWidth = 1;
      ctx.strokeRect(sx + 0.5, sy + 0.5, sw - 1, sh - 1);
      ctx.fillStyle = "#8d8aa0";
      ctx.font = "11px system-ui";
      ctx.fillText(s.name, sx, sy + sh + 12);
    }
  }

  return (
    <div className="col">
      <canvas ref={canvas} className="stage" style={{ height }} />
      {!liveStart && (
        <div className="row small">
          <button className="small" onClick={() => setPlaying((p) => !p)}>
            {playing ? "Pause" : "Play"}
          </button>
          <input
            type="range"
            className="grow"
            min={0}
            max={Math.max(duration, 1)}
            value={t}
            onChange={(e) => {
              tRef.current = Number(e.target.value);
              setT(tRef.current);
            }}
          />
          <span className="mono">
            {fmtDuration(t)} / {fmtDuration(duration)}
          </span>
        </div>
      )}
    </div>
  );
}
