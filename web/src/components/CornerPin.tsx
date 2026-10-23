import { useEffect, useRef, useState } from "react";
import { Pt } from "../api";
import CornerControls from "./CornerControls";

interface Props {
  corners: Pt[];
  aspect: number;
  onChange: (c: Pt[]) => void;
}

const LABELS = ["TL", "TR", "BR", "BL"];

/** Drag the four surface corners in projector output space; arrow keys nudge the selected one. */
export default function CornerPin({ corners, aspect, onChange }: Props) {
  const svg = useRef<SVGSVGElement>(null);
  const [sel, setSel] = useState(0);
  const dragging = useRef<number | null>(null);
  const W = 1000;
  const H = W / aspect;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      const step = (e.shiftKey ? 10 : e.altKey ? 0.25 : 1) / 1000;
      const d: Record<string, Pt> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step * aspect], ArrowDown: [0, step * aspect] };
      if (e.key in d) {
        e.preventDefault();
        const [dx, dy] = d[e.key];
        onChange(corners.map((c, i) => (i === sel ? [c[0] + dx, c[1] + dy] : c)) as Pt[]);
      } else if (e.key === "Tab") {
        e.preventDefault();
        setSel((s) => (s + (e.shiftKey ? 3 : 1)) % 4);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [corners, sel, onChange, aspect]);

  const toNorm = (e: React.PointerEvent): Pt => {
    const pt = svg.current!.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const p = pt.matrixTransform(svg.current!.getScreenCTM()!.inverse());
    return [p.x / W, p.y / H];
  };

  const pts = corners.map(([x, y]) => [x * W, y * H]);
  return (
    <div className="col">
      <svg
        ref={svg}
        viewBox={`-60 -60 ${W + 120} ${H + 120}`}
        style={{ width: "100%", background: "#08080c", borderRadius: 8, touchAction: "none" }}
        onPointerMove={(e) => {
          if (dragging.current == null) return;
          const p = toNorm(e);
          onChange(corners.map((c, i) => (i === dragging.current ? p : c)) as Pt[]);
        }}
        onPointerUp={() => (dragging.current = null)}
      >
        <rect x={0} y={0} width={W} height={H} fill="#000" stroke="#3a3a4a" strokeDasharray="8 6" />
        {[1, 2, 3].map((i) => (
          <g key={i} stroke="#15151d">
            <line x1={(W * i) / 4} x2={(W * i) / 4} y1={0} y2={H} />
            <line y1={(H * i) / 4} y2={(H * i) / 4} x1={0} x2={W} />
          </g>
        ))}
        <polygon points={pts.map((p) => p.join(",")).join(" ")} fill="rgba(255,122,26,0.15)" stroke="#ff7a1a" strokeWidth={3} />
        <line x1={pts[0][0]} y1={pts[0][1]} x2={pts[2][0]} y2={pts[2][1]} stroke="#ff7a1a55" />
        <line x1={pts[1][0]} y1={pts[1][1]} x2={pts[3][0]} y2={pts[3][1]} stroke="#ff7a1a55" />
        {pts.map(([x, y], i) => (
          <g key={i}>
            <circle
              cx={x}
              cy={y}
              r={18}
              fill={i === sel ? "#ff7a1a" : "#15151d"}
              stroke="#ff7a1a"
              strokeWidth={3}
              style={{ cursor: "grab" }}
              onPointerDown={(e) => {
                (e.target as Element).setPointerCapture(e.pointerId);
                dragging.current = i;
                setSel(i);
              }}
            />
            <text x={x} y={y + 5} fontSize={14} textAnchor="middle" fill={i === sel ? "#000" : "#fff"} pointerEvents="none">
              {LABELS[i]}
            </text>
          </g>
        ))}
      </svg>
      <div className="small muted">
        Selected {LABELS[sel]}: arrows nudge (Shift ×10, Alt fine), Tab cycles corners. Corners can go outside the frame.
      </div>
      <CornerControls corners={corners} selected={sel} onSelect={setSel} onChange={onChange} />
    </div>
  );
}
