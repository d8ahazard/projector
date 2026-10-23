import { useEffect, useRef, useState } from "react";
import { api, fmtDuration } from "../api";
import { useLive } from "../live";
import ProjectorPreview from "./ProjectorPreview";

interface Variant {
  id: string; title: string; relative_path: string; targets: string[]; appearance: string; orientation: string;
  character: string; content_rotation: number; review: boolean; buffer: boolean; status: string; error: string;
  thumb_url: string; preview_url: string; sheet_url: string; ready: boolean; asset_id: string | null;
  probable_copies: number; identical_locations: string[];
  preparation: string; preparation_error: string; match_reason: string;
  motion?: Motion;
  technical: { duration: number; fps: number; width: number; height: number; has_audio: boolean; motion?: Motion };
}
interface Motion { status?: string; enter_edge?: string; exit_edge?: string; travel?: string; confidence?: number; reason?: string }
interface Scene { id: string; title: string; collection: string; variants: Variant[] }
interface Result { items: Scene[]; total: number; page: number }
interface Job { id: string; kind: string; status: string; progress: number; error: string; detail: Record<string, unknown> }

export default function ScenePicker({ onSelect, initialTarget = "window", initialOrientation = "" }: { onSelect?: (id: string) => void; initialTarget?: string; initialOrientation?: string }) {
  const { toast } = useLive();
  const [filters, setFilters] = useState<Record<string, string>>({ target: initialTarget, orientation: initialOrientation });
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<Result>({ items: [], total: 0, page: 1 });
  const [facets, setFacets] = useState<{ collections: string[]; counts: Record<string, number>; total: number }>({ collections: [], counts: {}, total: 0 });
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState("");
  const detailPanel = useRef<HTMLDivElement>(null);
  useEffect(()=>{if(selected) detailPanel.current?.scrollIntoView({behavior:"smooth",block:"start"});},[selected]);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [mirror, setMirror] = useState(false);
  const query = new URLSearchParams({ ...filters, page: String(page), limit: "18" }).toString();
  useEffect(() => {
    let live = true;
    const load = () => Promise.all([api.get<Result>(`/api/library/scenes?${query}`), api.get<typeof facets>("/api/library/facets"), api.get<Job[]>("/api/library/jobs")]).then(([r, f, j]) => {
      if (live) { setResult(r); setFacets(f); setJobs(j); setError(""); }
    }).catch(e => { if (live) setError(String(e)); });
    void load(); const timer = window.setInterval(load, 4000);
    return () => { live = false; clearInterval(timer); };
  }, [query, revision]);
  const v = result.items.flatMap(s => s.variants).find(x => x.id === selected);
  const motion = v?.motion || v?.technical.motion;
  const change = (key: string, value: string) => { setFilters({ ...filters, [key]: value }); setPage(1); };
  const action = async (url: string, body?: unknown, method = "post") => {
    try { if (method === "patch") await api.patch(url, body); else await api.post(url, body); setRevision(r => r + 1); }
    catch (e) { toast((e as Error).message, true); }
  };
  const choose = (variant: Variant) => {
    setSelected(variant.id);
    if (!variant.thumb_url && variant.status === "indexed") void action(`/api/library/files/${variant.id}/preview`);
  };
  return <div className="col" style={{ gap: 14 }}>
    <div className="row"><div className="grow"><h2 style={{ margin: 0 }}>Find the right scene</h2><span className="muted small">{facets.counts.indexed || 0} indexed videos · {result.total} matching scenes · originals stay in place</span></div><button onClick={() => action("/api/library/motion")}>Analyze movement</button><button onClick={() => action("/api/library/scan")}>Scan AtmosFX</button></div>
    {error && <div role="alert" className="panel" style={{ color: "var(--bad)" }}>{error}</div>}
    <div className="row" style={{ flexWrap: "wrap" }}>
      <input placeholder="Search scenes, monsters, actions…" aria-label="Search scenes" value={filters.q || ""} onChange={e => change("q", e.target.value)} />
      <select aria-label="Projection target" value={filters.target || ""} onChange={e => change("target", e.target.value)}><option value="">Every target</option>{["window","wall","hollusion","doorway","tv","prop"].map(t => <option key={t}>{t}</option>)}</select>
      <select aria-label="Appearance" value={filters.appearance || ""} onChange={e => change("appearance", e.target.value)}><option value="">Every appearance</option>{["black","enclosure","shadow","unknown"].map(t => <option key={t}>{t}</option>)}</select>
      <select aria-label="Orientation" value={filters.orientation || ""} onChange={e => change("orientation", e.target.value)}><option value="">Every orientation</option><option>vertical</option><option>horizontal</option></select>
      <select aria-label="Collection" value={filters.collection || ""} onChange={e => change("collection", e.target.value)}><option value="">All collections</option>{facets.collections.map(t => <option key={t}>{t}</option>)}</select>
      <select aria-label="Readiness" value={filters.readiness || ""} onChange={e => change("readiness", e.target.value)}><option value="">Any readiness</option><option value="ready">Ready to play</option><option value="unprepared">Needs preparation</option><option value="review">Needs review</option></select>
      <input aria-label="Character" placeholder="Character" value={filters.character || ""} onChange={e => change("character",e.target.value)} style={{ width: 130 }} />
      <input aria-label="Maximum seconds" placeholder="Max seconds" type="number" min="0" value={filters.max_seconds || ""} onChange={e => change("max_seconds",e.target.value)} style={{ width: 120 }} />
      <label className="inline small"><input type="checkbox" checked={filters.buffers === "true"} onChange={e => change("buffers", String(e.target.checked))} />Include buffers</label>
    </div>
    {jobs.filter(j => j.status === "running" || j.status === "queued").slice(0,3).map(j => <div key={j.id} className="panel small">{j.kind} · {j.status} · {Math.round(j.progress * 100)}% {j.kind === "scan" && JSON.stringify(j.detail)}</div>)}
    {jobs.filter(j => j.status === "error").slice(0,2).map(j => <div key={j.id} className="small" role="alert" style={{ color: "var(--bad)" }}>{j.kind}: {j.error} <button onClick={()=>action(`/api/library/jobs/${j.id}/retry`)}>Retry failed preparation</button></div>)}
    <div className={onSelect ? "col scene-picker-layout" : "split scene-picker-layout"}>
      <div className="grid cards">{result.items.map(s => {
        const first = s.variants[0];
        return <div key={s.id} className="card" style={{ cursor: "pointer", borderColor: s.variants.some(x=>x.id===selected) ? "var(--accent)" : undefined }} onClick={() => choose(first)}>
          <div className="thumb" style={{ backgroundImage: first.thumb_url ? `url(${first.thumb_url})` : undefined, display: "grid", placeItems: "center" }}>{!first.thumb_url && <span className="muted small">Select to preview</span>}</div>
          <div className="body"><div className="title">{s.title}</div><div className="small muted">{s.collection}</div><div className="row small" style={{ flexWrap: "wrap", marginTop: 6 }}><span className="tag">{s.variants.length} variant{s.variants.length===1?"":"s"}</span><span className="tag">{first.appearance}</span><span>{fmtDuration((first.technical.duration || 0)*1000)}</span>{first.review && <span className="tag">Review orientation/type</span>}</div></div>
        </div>;
      })}</div>
      {v && <div ref={detailPanel} className="panel col scene-details" style={{ alignSelf: "start", minWidth: 0 }}>
        <h3 style={{ margin: 0 }}>{v.title}</h3>
        <select aria-label="Scene variant" value={v.id} onChange={e => { const next=result.items.flatMap(s=>s.variants).find(x=>x.id===e.target.value); if(next) choose(next); }}>{result.items.find(s=>s.variants.some(x=>x.id===v.id))?.variants.map(x=><option key={x.id} value={x.id}>{x.targets.join(" + ")} · {x.appearance} · {x.orientation} · {x.buffer ? "buffer" : "scene"}</option>)}</select>
        {v.preview_url ? <video key={v.preview_url+v.content_rotation} src={v.preview_url} controls loop style={{ width: "100%", maxHeight: 300, background: "black", transform: mirror ? "scaleX(-1)" : undefined }} /> : <button onClick={()=>action(`/api/library/files/${v.id}/preview`)}>Build action preview</button>}
        <label className="inline small"><input type="checkbox" checked={mirror} onChange={e=>setMirror(e.target.checked)} />Rear-projection mirror preview</label>
        {v.sheet_url && <img src={v.sheet_url} alt="Six sampled moments from this variant" style={{ width: "100%" }} />}
        <span className="small">{v.technical.width}×{v.technical.height} · {v.technical.fps?.toFixed(2)} native FPS · {v.technical.has_audio ? "with sound" : "silent"}</span>
        {motion ? <div className="row small" style={{flexWrap:"wrap"}}><span className="tag">enter {motion.enter_edge}</span><span className="tag">exit {motion.exit_edge}</span><span className="tag">{motion.travel}</span><span className="tag">{Math.round((motion.confidence || 0)*100)}% confidence</span>{motion.status === "ambiguous" && <span className="tag">Review movement</span>}</div> : <span className="small muted">Movement has not been analyzed yet.</span>}
        <span className="small muted">{v.match_reason}</span><span className="small" style={{ overflowWrap: "anywhere" }}>{v.relative_path}</span>
        {v.probable_copies > 1 && <span className="small muted">{v.probable_copies} probable copies by filename and size; identity is checked by hash during preparation.</span>}
        {v.identical_locations.length > 1 && <details><summary>{v.identical_locations.length} hash-confirmed identical locations</summary>{v.identical_locations.map(p=><div key={p} className="small">{p}</div>)}</details>}
        <label>Upright correction<select value={v.content_rotation} onChange={e=>action(`/api/library/files/${v.id}`,{content_rotation:Number(e.target.value),review:false},"patch")}>{[0,90,180,270].map(a=><option key={a} value={a}>{a}° clockwise</option>)}</select></label>
        <label>Appearance<select value={v.appearance} onChange={e=>action(`/api/library/files/${v.id}`,{appearance:e.target.value,review:false},"patch")}>{["black","enclosure","shadow","unknown"].map(t=><option key={t}>{t}</option>)}</select></label>
        <details><summary>Correct classification</summary><label>Scene title<input key={v.id+"title"} defaultValue={v.title} onBlur={e=>{if(e.target.value!==v.title) void action(`/api/library/files/${v.id}`,{title:e.target.value},"patch");}} /></label><label>Character<input key={v.id+"character"} defaultValue={v.character} onBlur={e=>{if(e.target.value!==v.character) void action(`/api/library/files/${v.id}`,{character:e.target.value},"patch");}} /></label><div className="row" style={{flexWrap:"wrap"}}>{["window","wall","doorway","tv","hollusion","prop"].map(t=><label key={t} className="inline"><input type="checkbox" checked={v.targets.includes(t)} onChange={e=>action(`/api/library/files/${v.id}`,{targets:e.target.checked?[...v.targets,t]:v.targets.filter(x=>x!==t)},"patch")} />{t}</label>)}</div></details>
        <details><summary>Correct movement</summary><div className="row" style={{flexWrap:"wrap"}}>{(["enter_edge","exit_edge","travel"] as const).map(key=><label key={key}>{key.replace("_"," ")}<select value={motion?.[key] || (key === "travel" ? "stationary" : "none")} onChange={e=>action(`/api/library/files/${v.id}`,{motion:{...(motion || {}),[key]:e.target.value,status:"ready",confidence:1}},"patch")}>{(key === "travel" ? ["stationary","left-right","right-left","top-bottom","bottom-top"] : ["none","left","right","top","bottom"]).map(x=><option key={x}>{x}</option>)}</select></label>)}</div><span className="small muted">Corrections override the automatic analysis. Horizontal flips happen per clip; vertical movement is scheduled as detected and never rotated sideways.</span></details>
        {(v.error || v.preparation_error) && <div role="alert" style={{color:"var(--bad)"}}>{v.error || v.preparation_error}</div>}
        <div className="row"><button disabled={v.status!=="indexed" || ["pending","processing"].includes(v.preparation)} onClick={()=>action(`/api/library/files/${v.id}/prepare`)}>{v.ready ? "Prepared" : v.preparation === "unprepared" ? "Prepare for playback" : v.preparation}</button>{onSelect && <button className="primary" disabled={!v.ready || !v.asset_id} onClick={()=>v.asset_id && onSelect(v.asset_id)}>Use scene · auto-fit outputs</button>}</div>
        {v.ready && v.asset_id && <ProjectorPreview assetId={v.asset_id} label="Play scene on projector" />}
        {onSelect && <span className="small muted">The saved scene is the anchor. At playback, each calibrated projector receives the ready horizontal or vertical sibling that fits its output.</span>}
        <button disabled={v.status!=="indexed"} onClick={()=>action(`/api/library/files/${v.id}/prepare?profile=interpolated60`)}>Prepare optional RIFE 60 FPS rendition</button>
        <span className="small muted">RIFE uses the local GPU and may take time. It processes bounded chunks and detects cuts before interpolating. Preparation preserves source cadence unless you choose interpolation. Playback compatibility remains unverified until checked on your projector.</span>
      </div>}
    </div>
    {!result.items.length && <div className="panel muted">{facets.total ? "No scenes match these filters." : "Scan your AtmosFX folder to populate the catalogue."}</div>}
    <div className="row"><button disabled={page===1} onClick={()=>setPage(page-1)}>Previous</button><span className="small">Page {page} of {Math.max(1,Math.ceil(result.total/18))}</span><button disabled={page*18>=result.total} onClick={()=>setPage(page+1)}>Next</button></div>
  </div>;
}
