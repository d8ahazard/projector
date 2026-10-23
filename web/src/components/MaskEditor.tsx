import { useRef, useState } from "react";
import { Pt } from "../api";

interface Props {
  mask: Pt[];
  aspect: number;
  onChange: (m: Pt[]) => void;
}

/** Polygon mask in surface coordinates: click to add points, drag to move, right-click to delete. */
export default function MaskEditor({ mask, aspect, onChange }: Props) {
  const svg = useRef<SVGSVGElement>(null);
  const dragging = useRef<number | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const H = 400;
  const W = H * aspect;

  const toNorm = (e: React.PointerEvent | React.MouseEvent): Pt => {
    const pt = svg.current!.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const p = pt.matrixTransform(svg.current!.getScreenCTM()!.inverse());
    return [Math.min(1, Math.max(0, p.x / W)), Math.min(1, Math.max(0, p.y / H))];
  };

  return (
    <div className="col">
      <svg
        ref={svg}
        viewBox={`-10 -10 ${W + 20} ${H + 20}`}
        style={{ width: "100%", maxHeight: 360, background: "#08080c", borderRadius: 8, touchAction: "none" }}
        onPointerMove={(e) => {
          if (dragging.current == null) return;
          const p = toNorm(e);
          onChange(mask.map((m, i) => (i === dragging.current ? p : m)) as Pt[]);
        }}
        onPointerUp={() => (dragging.current = null)}
      >
        <rect
          x={0}
          y={0}
          width={W}
          height={H}
          fill="#15151d"
          stroke="#3a3a4a"
          onClick={(e) => onChange([...mask, toNorm(e)])}
        />
        {mask.length >= 3 && (
          <polygon points={mask.map(([x, y]) => `${x * W},${y * H}`).join(" ")} fill="rgba(122,215,255,0.2)" stroke="#7ad7ff" strokeWidth={2} pointerEvents="none" />
        )}
        {mask.length > 0 && mask.length < 3 && (
          <polyline points={mask.map(([x, y]) => `${x * W},${y * H}`).join(" ")} fill="none" stroke="#7ad7ff" strokeWidth={2} pointerEvents="none" />
        )}
        {mask.map(([x, y], i) => (
          <circle
            key={i}
            cx={x * W}
            cy={y * H}
            r={hover === i ? 9 : 7}
            fill="#7ad7ff"
            style={{ cursor: "grab" }}
            onPointerEnter={() => setHover(i)}
            onPointerLeave={() => setHover(null)}
            onPointerDown={(e) => {
              e.stopPropagation();
              (e.target as Element).setPointerCapture(e.pointerId);
              dragging.current = i;
            }}
            onContextMenu={(e) => {
              e.preventDefault();
              onChange(mask.filter((_, j) => j !== i));
            }}
          />
        ))}
      </svg>
      <div className="row small">
        <span className="muted">{mask.length < 3 ? "No mask: the whole surface is used." : `${mask.length} points.`}</span>
        <button className="small right" onClick={() => onChange([])}>
          Clear mask
        </button>
      </div>
    </div>
  );
}
