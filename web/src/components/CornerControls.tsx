import { useState } from "react";
import { Pt } from "../api";

const LABELS = ["Top left", "Top right", "Bottom right", "Bottom left"];

interface Props {
  corners: Pt[];
  selected: number;
  onSelect: (index: number) => void;
  onChange: (corners: Pt[]) => void;
}

/** Phone-friendly corner editing. Dragging remains available, but is never required. */
export default function CornerControls({ corners, selected, onSelect, onChange }: Props) {
  const [step, setStep] = useState(0.002);
  const point = corners[selected] ?? [0, 0];
  const setPoint = (x: number, y: number) => onChange(corners.map((p, i) => i === selected ? [x, y] as Pt : p));
  const nudge = (dx: number, dy: number) => setPoint(point[0] + dx, point[1] + dy);
  const buttonStyle = { minWidth: 48, minHeight: 44, fontSize: 20, padding: "6px 14px" };

  return <div className="col" style={{ gap: 10 }}>
    <div className="row" style={{ flexWrap: "wrap" }}>
      {LABELS.map((label, index) => <button key={label} type="button" className={selected === index ? "primary" : ""} onClick={() => onSelect(index)}>{label}</button>)}
    </div>
    <div className="row" style={{ alignItems: "center", flexWrap: "wrap", gap: 12 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 48px)", gridTemplateRows: "repeat(3, 44px)", gap: 5 }} aria-label={`Nudge ${LABELS[selected]}`}>
        <span /><button type="button" style={buttonStyle} aria-label="Move up" onClick={() => nudge(0, -step)}>↑</button><span />
        <button type="button" style={buttonStyle} aria-label="Move left" onClick={() => nudge(-step, 0)}>←</button>
        <button type="button" style={{ ...buttonStyle, fontSize: 12 }} title="Reset this point" onClick={() => setPoint(selected === 0 || selected === 3 ? 0 : 1, selected < 2 ? 0 : 1)}>Reset</button>
        <button type="button" style={buttonStyle} aria-label="Move right" onClick={() => nudge(step, 0)}>→</button>
        <span /><button type="button" style={buttonStyle} aria-label="Move down" onClick={() => nudge(0, step)}>↓</button><span />
      </div>
      <div className="col grow" style={{ minWidth: 180 }}>
        <label className="small">Nudge amount
          <select value={step} onChange={e => setStep(Number(e.target.value))}>
            <option value={0.0005}>Fine · 0.05%</option>
            <option value={0.002}>Normal · 0.2%</option>
            <option value={0.01}>Coarse · 1%</option>
          </select>
        </label>
        <div className="row">
          <label className="small grow">X %<input inputMode="decimal" type="number" step="0.05" value={(point[0] * 100).toFixed(2)} onChange={e => setPoint(Number(e.target.value) / 100, point[1])} /></label>
          <label className="small grow">Y %<input inputMode="decimal" type="number" step="0.05" value={(point[1] * 100).toFixed(2)} onChange={e => setPoint(point[0], Number(e.target.value) / 100)} /></label>
        </div>
      </div>
    </div>
    <span className="small muted">Select a corner, then tap the arrows. Values may go outside 0–100% when the physical edge lies beyond the projected frame.</span>
  </div>;
}
