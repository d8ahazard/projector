import { useMemo, useState } from "react";
import ScenePicker from "./ScenePicker";
import { Cue, fileUrl, fmtDuration, Keyframe, MediaAsset, Pattern, Surface, Transition, TransitionType } from "../api";

interface Props {
  cue: Cue;
  assets: MediaAsset[];
  surfaces: Surface[];
  groups: string[];
  onChange: (c: Cue) => void;
}

export const PATTERNS: { id: Pattern; label: string; help: string }[] = [
  { id: "together", label: "Together", help: "Same clip on every target at the same instant." },
  { id: "each", label: "Each", help: "A different clip on each target, all at once." },
  { id: "sequence", label: "Sequence", help: "One target after another, e.g. left then right." },
  { id: "span", label: "Span", help: "One video stretched across the whole house; each window shows its slice." },
  { id: "chase", label: "Chase", help: "Repeat the sequence in the same direction for N passes." },
  { id: "ping-pong", label: "Ping-pong", help: "Sequence back and forth for N passes." },
  { id: "random-one", label: "Random one", help: "Pick one random target per repetition (no immediate repeats)." },
];

export const TRANSITIONS: { id: TransitionType; label: string }[] = [
  { id: "cut", label: "Cut" },
  { id: "dip", label: "Dip to black" },
  { id: "crossfade", label: "Crossfade" },
  { id: "wipe", label: "Wipe across house" },
  { id: "cascade", label: "Cascade" },
  { id: "ripple-out", label: "Ripple out" },
  { id: "ripple-in", label: "Ripple in" },
];

const SPAN_PRESETS: Record<string, Keyframe[]> = {
  "Fly left → right": [
    { t: 0, x: -0.4, y: 0, w: 0.4, h: 1, opacity: 1 },
    { t: 8, x: 1, y: 0, w: 0.4, h: 1, opacity: 1 },
  ],
  "Fly right → left": [
    { t: 0, x: 1, y: 0, w: 0.4, h: 1, opacity: 1 },
    { t: 8, x: -0.4, y: 0, w: 0.4, h: 1, opacity: 1 },
  ],
  "Rise from below": [
    { t: 0, x: 0, y: 1, w: 1, h: 1, opacity: 0 },
    { t: 4, x: 0, y: 0, w: 1, h: 1, opacity: 1 },
  ],
  "Fill house": [{ t: 0, x: 0, y: 0, w: 1, h: 1, opacity: 1 }],
};

export function summarizeCue(c: Cue, assets: MediaAsset[], surfaces: Surface[]): string {
  if(c.type === "encounter") return `Encounter · ${c.tracks?.length || 0} timed clips · ${Array.from(new Set(c.tracks?.map(t=>surfaces.find(s=>s.id===t.surface_id)?.name || "missing surface"))).join(" → ")}`;
  const t = c.target ?? { mode: "all" };
  const target =
    t.mode === "group" ? `group "${t.group}"` : t.mode === "list" ? (t.surfaces ?? []).map((id) => surfaces.find((s) => s.id === id)?.name ?? "?").join(" → ") : "all windows";
  const clips = c.clips.length
    ? c.clips.map((id) => assets.find((a) => a.id === id)?.title ?? "missing").join(", ")
    : c.pool?.tags?.length || c.pool?.season
      ? `random from ${[c.pool?.season, ...(c.pool?.tags ?? [])].filter(Boolean).join("+")}`
      : "no clips";
  return `${c.pattern} · ${target} · ${clips}`;
}

export default function CueEditor({ cue, assets, surfaces, groups, onChange }: Props) {
  const [search, setSearch] = useState("");
  const set = <K extends keyof Cue>(k: K, v: Cue[K]) => onChange({ ...cue, [k]: v });
  const tr: Transition = { type: "dip", duration: 0.8, easing: "ease-in-out", ...(cue.transition ?? {}) };
  const setTr = (patch: Partial<Transition>) => set("transition", { ...tr, ...patch });
  const target = cue.target ?? { mode: "all" };
  const selectedSurface = surfaces.find(s => target.mode === "list" ? target.surfaces?.includes(s.id) : target.mode === "group" ? s.groups.includes(target.group || "") : true);
  const spacing = cue.spacing ?? { mode: "after" };
  const duration = cue.duration ?? { mode: "clip" };
  const videos = useMemo(() => assets.filter((a) => a.kind === "video" && a.status === "ready"), [assets]);
  const filtered = videos.filter((a) => !search || a.title.toLowerCase().includes(search.toLowerCase()) || a.tags.some((t) => t.includes(search.toLowerCase())));
  const isSeq = ["sequence", "chase", "ping-pong"].includes(cue.pattern);
  const allTags = useMemo(() => Array.from(new Set(videos.flatMap((a) => a.tags))).sort(), [videos]);

  return (
    <div className="col">
      <label>
        Name
        <input value={cue.name ?? ""} placeholder={cue.pattern} onChange={(e) => set("name", e.target.value)} />
      </label>

      <h3>Pattern</h3>
      <div className="row" style={{ gap: 4 }}>
        {PATTERNS.map((p) => (
          <button key={p.id} className={`small ${cue.pattern === p.id ? "primary" : ""}`} title={p.help} onClick={() => set("pattern", p.id)}>
            {p.label}
          </button>
        ))}
      </div>
      <span className="small muted">{PATTERNS.find((p) => p.id === cue.pattern)?.help}</span>
      {["together","span"].includes(cue.pattern) && <details><summary>Sound routing</summary><label>Designated sound source<select value={cue.audio_surface_id || ""} onChange={e=>set("audio_surface_id",e.target.value || undefined)}><option value="">First targeted surface</option>{surfaces.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>{surfaces.map(s=><label key={s.id}>{s.name}<select value={cue.audio_overrides?.[s.id] === undefined ? "default" : String(cue.audio_overrides[s.id])} onChange={e=>{const overrides={...cue.audio_overrides};if(e.target.value==="default") delete overrides[s.id];else overrides[s.id]=e.target.value==="true";set("audio_overrides",overrides);}}><option value="default">Use designated source</option><option value="true">Sound on</option><option value="false">Sound off</option></select></label>)}</details>}

      <h3>Clips</h3>
      <details><summary>Browse AtmosFX scenes and variants</summary><ScenePicker key={selectedSurface?.id} initialOrientation={selectedSurface ? selectedSurface.height > selectedSurface.width ? "vertical" : "horizontal" : ""} onSelect={id => set("clips", [...cue.clips, id])} /></details>
      <div className="col" style={{ gap: 4 }}>
        {cue.clips.map((id, i) => {
          const a = assets.find((x) => x.id === id);
          return (
            <div key={`${id}-${i}`} className="row small">
              <span style={{ width: 48, height: 27, background: `#000 url(${fileUrl(a?.thumb_path ?? "")}) center/cover`, borderRadius: 3 }} />
              <span className="grow">{a?.title ?? "missing clip"}</span>
              <span className="muted">{a ? fmtDuration(a.duration_ms) : ""}</span>
              <button className="small" disabled={i === 0} onClick={() => set("clips", swap(cue.clips, i, i - 1))}>
                ↑
              </button>
              <button className="small" onClick={() => set("clips", cue.clips.filter((_, j) => j !== i))}>
                ✕
              </button>
            </div>
          );
        })}
      </div>
      <input placeholder="Search library to add clips" value={search} onChange={(e) => setSearch(e.target.value)} />
      <div style={{ maxHeight: 180, overflowY: "auto", display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(96px,1fr))", gap: 6 }}>
        {filtered.slice(0, 60).map((a) => (
          <div key={a.id} title={a.title} onClick={() => set("clips", [...cue.clips, a.id])} style={{ cursor: "pointer" }}>
            <div style={{ aspectRatio: "16/9", background: `#000 url(${fileUrl(a.thumb_path)}) center/cover`, borderRadius: 4 }} />
            <div className="small" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {a.title}
            </div>
          </div>
        ))}
      </div>
      <span className="small muted">
        {cue.pattern === "each" || isSeq ? "Multiple clips are assigned to targets in order." : "Multiple clips rotate on each repeat."} With no clips, a random pool is used:
      </span>
      <div className="row">
        <select value={cue.pool?.season ?? ""} onChange={(e) => set("pool", { ...cue.pool, season: e.target.value })}>
          <option value="">Any season</option>
          <option value="halloween">Halloween</option>
          <option value="christmas">Christmas</option>
        </select>
        <input
          list="cue-tags"
          placeholder="pool tags (comma)"
          defaultValue={(cue.pool?.tags ?? []).join(", ")}
          key={cue.id}
          onBlur={(e) => set("pool", { ...cue.pool, tags: e.target.value.split(",").map((t) => t.trim()).filter(Boolean) })}
        />
        <datalist id="cue-tags">
          {allTags.map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>
      </div>

      <h3>Targets</h3>
      <div className="row">
        <select value={target.mode} onChange={(e) => set("target", { ...target, mode: e.target.value as "all" | "group" | "list" })}>
          <option value="all">All windows</option>
          <option value="group">Group</option>
          <option value="list">Pick windows</option>
        </select>
        {target.mode === "group" && (
          <select value={target.group ?? ""} onChange={(e) => set("target", { ...target, group: e.target.value })}>
            <option value="">choose group</option>
            {groups.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
        )}
      </div>
      {target.mode === "list" && (
        <div className="row small">
          {surfaces.map((s) => {
            const list = target.surfaces ?? [];
            const idx = list.indexOf(s.id);
            return (
              <label key={s.id} className="inline">
                <input
                  type="checkbox"
                  checked={idx >= 0}
                  onChange={(e) => set("target", { ...target, surfaces: e.target.checked ? [...list, s.id] : list.filter((x) => x !== s.id) })}
                />
                {s.name}
                {idx >= 0 && <span className="tag">{idx + 1}</span>}
              </label>
            );
          })}
        </div>
      )}

      {isSeq && (
        <>
          <h3>Sequence</h3>
          <div className="field-row">
            <label>
              Direction
              <select value={cue.direction ?? "left-right"} onChange={(e) => set("direction", e.target.value as Cue["direction"])}>
                <option value="left-right">Left → right</option>
                <option value="right-left">Right → left</option>
                <option value="top-bottom">Top → bottom</option>
                <option value="bottom-top">Bottom → top</option>
                <option value="list">Picked order</option>
              </select>
            </label>
            <label>
              Spacing
              <select value={spacing.mode} onChange={(e) => set("spacing", { ...spacing, mode: e.target.value as "after" | "stagger" | "travel" })}>
                <option value="after">After previous</option>
                <option value="stagger">Fixed stagger</option>
                <option value="travel">Travel (real distance)</option>
              </select>
            </label>
            {spacing.mode === "after" && (
              <label>
                Gap (s, negative overlaps)
                <input type="number" step={0.1} value={spacing.gap ?? 0} onChange={(e) => set("spacing", { ...spacing, gap: Number(e.target.value) })} />
              </label>
            )}
            {spacing.mode === "stagger" && (
              <label>
                Every (s)
                <input type="number" step={0.1} value={spacing.stagger ?? 1} onChange={(e) => set("spacing", { ...spacing, stagger: Number(e.target.value) })} />
              </label>
            )}
            {spacing.mode === "travel" && (
              <>
                <label>
                  Speed (ft/s)
                  <input type="number" step={0.5} value={spacing.speed ?? 4} onChange={(e) => set("spacing", { ...spacing, speed: Number(e.target.value) })} />
                </label>
                <label>
                  Exits clip at (s)
                  <input
                    type="number"
                    step={0.1}
                    placeholder="clip end"
                    value={spacing.exit_at ?? ""}
                    onChange={(e) => set("spacing", { ...spacing, exit_at: e.target.value === "" ? undefined : Number(e.target.value) })}
                  />
                </label>
                <label>
                  Enters clip at (s)
                  <input type="number" step={0.1} value={spacing.enter_at ?? 0} onChange={(e) => set("spacing", { ...spacing, enter_at: Number(e.target.value) })} />
                </label>
              </>
            )}
            {cue.pattern !== "sequence" && (
              <label>
                Passes
                <input type="number" min={1} value={cue.passes ?? 2} onChange={(e) => set("passes", Number(e.target.value))} />
              </label>
            )}
          </div>
        </>
      )}
      {cue.pattern === "random-one" && (
        <label>
          Picks
          <input type="number" min={1} value={cue.passes ?? 1} onChange={(e) => set("passes", Number(e.target.value))} />
        </label>
      )}

      {cue.pattern === "span" && (
        <>
          <h3>Motion across the house</h3>
          <div className="row">
            {Object.keys(SPAN_PRESETS).map((k) => (
              <button key={k} className="small" onClick={() => set("keyframes", SPAN_PRESETS[k])}>
                {k}
              </button>
            ))}
            <button className="small" onClick={() => set("keyframes", [])}>
              Auto fit
            </button>
          </div>
          <span className="small muted">x, y, w, h are fractions of the combined window area; t is seconds into the clip.</span>
          {(cue.keyframes ?? []).map((k, i) => (
            <div key={i} className="row small">
              {(["t", "x", "y", "w", "h", "opacity"] as const).map((f) => (
                <label key={f} style={{ width: 62 }}>
                  {f}
                  <input
                    type="number"
                    step={0.05}
                    value={k[f] ?? (f === "opacity" ? 1 : 0)}
                    onChange={(e) => set("keyframes", (cue.keyframes ?? []).map((kk, j) => (j === i ? { ...kk, [f]: Number(e.target.value) } : kk)))}
                  />
                </label>
              ))}
              <button className="small" onClick={() => set("keyframes", (cue.keyframes ?? []).filter((_, j) => j !== i))}>
                ✕
              </button>
            </div>
          ))}
          <button
            className="small"
            style={{ alignSelf: "flex-start" }}
            onClick={() => {
              const kfs = cue.keyframes ?? [];
              const last = kfs[kfs.length - 1] ?? { t: 0, x: 0, y: 0, w: 1, h: 1, opacity: 1 };
              set("keyframes", [...kfs, { ...last, t: last.t + 2 }]);
            }}
          >
            Add keyframe
          </button>
        </>
      )}

      <h3>Timing</h3>
      <div className="field-row">
        <label>
          Duration
          <select value={duration.mode} onChange={(e) => set("duration", { ...duration, mode: e.target.value as "clip" | "fixed" | "loops" })}>
            <option value="clip">Clip length</option>
            <option value="fixed">Fixed seconds</option>
            <option value="loops">Loop N times</option>
          </select>
        </label>
        {duration.mode === "fixed" && (
          <label>
            Seconds
            <input type="number" value={duration.seconds ?? 30} onChange={(e) => set("duration", { ...duration, seconds: Number(e.target.value) })} />
          </label>
        )}
        {duration.mode === "loops" && (
          <label>
            Loops
            <input type="number" min={1} value={duration.loops ?? 2} onChange={(e) => set("duration", { ...duration, loops: Number(e.target.value) })} />
          </label>
        )}
        <label>
          Repeat cue
          <input type="number" min={1} value={cue.repeat ?? 1} onChange={(e) => set("repeat", Number(e.target.value))} />
        </label>
        <label>
          Hold after (s)
          <input type="number" min={0} step={0.5} value={cue.hold ?? 0} onChange={(e) => set("hold", Number(e.target.value))} />
        </label>
        <label>
          Fit
          <select value={cue.fit ?? "contain"} onChange={(e) => set("fit", e.target.value as Cue["fit"])}>
            <option value="contain">Contain</option>
            <option value="cover">Cover</option>
            <option value="stretch">Stretch</option>
          </select>
        </label>
      </div>

      <h3>Transition in</h3>
      <div className="field-row">
        <label>
          Type
          <select value={tr.type} onChange={(e) => setTr({ type: e.target.value as TransitionType })}>
            {TRANSITIONS.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Duration (s)
          <input type="number" step={0.1} min={0} value={tr.duration} onChange={(e) => setTr({ duration: Number(e.target.value) })} />
        </label>
        <label>
          Easing
          <select value={tr.easing ?? "ease-in-out"} onChange={(e) => setTr({ easing: e.target.value as Transition["easing"] })}>
            <option value="ease-in-out">Ease in-out</option>
            <option value="linear">Linear</option>
            <option value="ease-in">Ease in</option>
            <option value="ease-out">Ease out</option>
          </select>
        </label>
        {(tr.type === "wipe" || tr.type === "cascade") && (
          <label>
            Direction
            <select value={tr.direction ?? "right"} onChange={(e) => setTr({ direction: e.target.value as Transition["direction"] })}>
              <option value="right">Left → right</option>
              <option value="left">Right → left</option>
              <option value="down">Top → bottom</option>
              <option value="up">Bottom → top</option>
            </select>
          </label>
        )}
        {["wipe", "cascade", "ripple-out", "ripple-in"].includes(tr.type) && (
          <label>
            Spread across house (s)
            <input type="number" step={0.1} min={0} value={tr.spread ?? 1.5} onChange={(e) => setTr({ spread: Number(e.target.value) })} />
          </label>
        )}
        {["cascade", "ripple-out", "ripple-in"].includes(tr.type) && (
          <label>
            Each window
            <select value={tr.local ?? "dip"} onChange={(e) => setTr({ local: e.target.value as Transition["local"] })}>
              <option value="dip">Dips</option>
              <option value="crossfade">Crossfades</option>
              <option value="cut">Cuts</option>
            </select>
          </label>
        )}
      </div>
      {cue.offsets && Object.keys(cue.offsets).length > 0 && (
        <div className="row small">
          <span className="muted">Timeline offsets: {Object.entries(cue.offsets).map(([s, v]) => `${surfaces.find((x) => x.id === s)?.name ?? s} ${v > 0 ? "+" : ""}${v.toFixed(2)}s`).join(", ")}</span>
          <button className="small" onClick={() => set("offsets", {})}>
            Clear
          </button>
        </div>
      )}
    </div>
  );
}

function swap<T>(arr: T[], i: number, j: number): T[] {
  const a = [...arr];
  [a[i], a[j]] = [a[j], a[i]];
  return a;
}
