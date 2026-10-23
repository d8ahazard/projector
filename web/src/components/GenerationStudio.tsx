import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fileUrl, MediaAsset, Surface } from "../api";
import { useLive } from "../live";

interface Project { id: string; name: string; concept: string; character_design: string; orientation: string; style: string; background: string; revision: number; character_take_id: string | null }
interface Shot { id: string; position: number; name: string; prompt: string; background: string; sound: string; seconds: number; role: string; surface: string; handoff: string; revision: number; image_take_id: string | null }
interface Take { id: string; stage: string; shot_id: string | null; status: string; error: string; approved: boolean; current: boolean; elapsed_seconds: number; provenance?: {motion_prompt?: string; set_name?: string; sequence_index?: number}; outputs: {url: string; local: string}[]; asset: MediaAsset & {published: boolean} | null }
interface Detail { project: Project; shots: Shot[]; takes: Take[] }
type Action = (url: string, body?: unknown, method?: string) => Promise<void>;

function ShotEditor({ shot, projectId, characterReady, action }: { shot: Shot; projectId: string; characterReady: boolean; action: Action }) {
  const [draft, setDraft] = useState(shot);
  const [backend, setBackend] = useState("ltx");
  const [motionPrompt, setMotionPrompt] = useState("");
  const [setName, setSetName] = useState("");
  const [sequenceIndex, setSequenceIndex] = useState(shot.position ?? 0);
  const [travel, setTravel] = useState("");
  const dirty = JSON.stringify(draft) !== JSON.stringify(shot);
  return <div className="panel col">
    <div className="row"><input className="grow" aria-label="Beat name" value={draft.name} onChange={e=>setDraft({...draft,name:e.target.value})} /><span className="tag">revision {shot.revision}</span></div>
    <textarea aria-label="Action prompt" value={draft.prompt} onChange={e=>setDraft({...draft,prompt:e.target.value})} rows={3} placeholder="The creature slowly leans into view, then reaches toward the glass…" />
    <label>Scene background<select value={draft.background || ""} onChange={e=>setDraft({...draft,background:e.target.value})}><option value="">Use project background</option><option value="black">Isolated on black</option><option value="enclosure">Dark enclosure</option></select></label><label>Sound<textarea value={draft.sound} onChange={e=>setDraft({...draft,sound:e.target.value})} rows={2} placeholder="Low breath, claws scraping glass, a quiet growl…" /></label>
    <div className="row" style={{flexWrap:"wrap"}}><select aria-label="Beat role" value={draft.role} onChange={e=>setDraft({...draft,role:e.target.value})}>{["entrance","action","idle","exit"].map(x=><option key={x}>{x}</option>)}</select><select aria-label="Take duration" value={draft.seconds} onChange={e=>setDraft({...draft,seconds:Number(e.target.value)})}><option value={5}>5 seconds</option><option value={10}>10 seconds</option></select><label>Window<input style={{width:70}} value={draft.surface} onChange={e=>setDraft({...draft,surface:e.target.value})} /></label><select aria-label="Handoff" value={draft.handoff} onChange={e=>setDraft({...draft,handoff:e.target.value})}><option value="darkness">Vanish through darkness</option><option value="travel">Directional travel</option><option value="effect">Effect-assisted handoff</option></select></div>
    <div className="row" style={{flexWrap:"wrap"}}><button disabled={!dirty} onClick={()=>action(`/api/generation/shots/${shot.id}`,draft,"put")}>Save beat</button><button disabled={!characterReady || dirty} onClick={()=>action(`/api/generation/projects/${projectId}/render`,{stage:"image",shot_id:shot.id})}>Generate starting image</button><select aria-label="Video model" value={backend} onChange={e=>setBackend(e.target.value)}><option value="ltx">LTX-2.3 + sound</option><option value="wan">Wan 2.2 + MMAudio</option></select><button className="primary" disabled={!shot.image_take_id || dirty} onClick={()=>action(`/api/generation/projects/${projectId}/render`,{stage:"video",shot_id:shot.id,backend,motion_prompt:motionPrompt,set_name:setName,sequence_index:sequenceIndex,travel_direction:travel.split(":")[0],travel_phase:travel.split(":")[1] || ""})}>Animate approved image</button></div>
    {dirty && <span className="small muted">Saving edits invalidates this beat’s image approval. Existing takes remain available.</span>}
    {shot.image_take_id && <details><summary>Refine motion for the next take</summary><label>Animation direction<textarea rows={3} maxLength={4000} value={motionPrompt} onChange={e=>setMotionPrompt(e.target.value)} placeholder="Optional: keep the face illuminated and slowly raise one hand…" /></label><div className="row"><label>Take set<input maxLength={120} value={setName} onChange={e=>setSetName(e.target.value)} placeholder="Original encounter" /></label><label>Step<input type="number" min={1} max={101} value={sequenceIndex+1} onChange={e=>setSequenceIndex(Number(e.target.value)-1)} /></label><label>Window crossing<select value={travel} onChange={e=>setTravel(e.target.value)}><option value="">None</option><option value="right:exit">Exit right edge</option><option value="right:enter">Enter from left</option><option value="left:exit">Exit left edge</option><option value="left:enter">Enter from right</option></select></label></div><span className="small muted">Uses the approved starting image. Crossing requires a black background and is baked into the clip. Each new take needs motion and sound review.</span></details>}
  </div>;
}

function ProjectEditor({ project, action }: { project: Project; action: Action }) {
  const [draft, setDraft] = useState(project);
  return <div className="panel col"><label>Project name<input value={draft.name} onChange={e=>setDraft({...draft,name:e.target.value})} /></label><label>Concept<textarea rows={3} value={draft.concept} onChange={e=>setDraft({...draft,concept:e.target.value})} placeholder="A lanky swamp creature lurks behind the windows, reacting to people outside…" /></label><label>Character appearance (one isolated creature)<textarea rows={3} value={draft.character_design || ""} onChange={e=>setDraft({...draft,character_design:e.target.value})} placeholder="Describe anatomy, face, materials, colors and clothing; leave window handoffs in the concept. The planner can propose this." /></label><label>Framing<select value={draft.orientation} onChange={e=>setDraft({...draft,orientation:e.target.value})}><option value="vertical">Portrait / window</option><option value="horizontal">Landscape / wall</option></select></label><label>Consistent style<textarea rows={2} value={draft.style} onChange={e=>setDraft({...draft,style:e.target.value})} /></label><label>Background<select value={draft.background} onChange={e=>setDraft({...draft,background:e.target.value})}><option value="black">Creature emerging from black</option><option value="enclosure">Dark containment enclosure</option></select></label><button disabled={JSON.stringify(draft)===JSON.stringify(project)} onClick={()=>action(`/api/generation/projects/${project.id}`,draft,"put")}>Save concept and style</button><div className="small muted">Save before generating. Changing the concept or style requires a new character approval.</div></div>;
}

export default function GenerationStudio() {
  const { toast } = useLive();
  const [projects, setProjects] = useState<Project[]>([]);
  const [id, setId] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [surfaces, setSurfaces] = useState<Surface[]>([]);
  const [mapping, setMapping] = useState<Record<string,string>>({});
  const [capabilities, setCapabilities] = useState<Record<string,{ready:boolean;error:string}>>({});
  useEffect(()=>{api.get<Record<string,{ready:boolean;error:string}>>("/api/generation/capabilities").then(setCapabilities).catch(()=>{});},[]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [programId, setProgramId] = useState("");
  const [exportSet, setExportSet] = useState("");
  useEffect(()=>{
    let live=true;
    const load=async()=>{
      try {
        const [p,l]=await Promise.all([api.get<Project[]>("/api/generation/projects"),api.get<{surfaces:Surface[]}>("/api/layout")]);
        if(!live) return; setProjects(p);setSurfaces(l.surfaces);
        const current=id || p[0]?.id;
        if(current) {const d=await api.get<Detail>(`/api/generation/projects/${current}`);if(live) setDetail(d);}
      } catch(e) {if(live) setError((e as Error).message);}
    };
    void load(); const timer=setInterval(load,4000);return()=>{live=false;clearInterval(timer);};
  },[id,revision]);
  const action: Action = async(url,body,method="post")=>{
    setBusy(true);setError("");
    try {if(url.endsWith("/render")) {const c=await api.get<Record<string,{ready:boolean;error:string}>>("/api/generation/capabilities");setCapabilities(c);const b=body as {stage:string;backend?:string};const k=b.stage === "video" ? b.backend || "ltx" : "image";if(!c[k]?.ready) throw new Error(c[k]?.error || "Workflow dependencies unavailable");} if(method==="put") await api.put(url,body);else await api.post(url,body);setRevision(r=>r+1);}
    catch(e){setError((e as Error).message);toast((e as Error).message,true);}finally{setBusy(false);}
  };
  const create=async()=>{try{const p=await api.post<Project>("/api/generation/projects",{name:"New creature encounter",concept:""});setId(p.id);}catch(e){setError((e as Error).message);}};
  const p=detail?.project;
  const exportEncounter=async()=>{if(!p)return;try{const program=await api.post<{id:string}>(`/api/generation/projects/${p.id}/encounter`,{surfaces:mapping,set_name:exportSet});setProgramId(program.id);toast("Encounter added to Programs");}catch(e){setError((e as Error).message);}};
  return <div className="col" style={{gap:18}}>
    <div className="row"><div className="grow"><h1 style={{margin:0}}>Creature studio</h1><span className="muted">Concept → character → approved starting images → motion + sound → window encounter</span></div><button onClick={create}>New encounter</button></div>
    <div className="panel row small" style={{flexWrap:"wrap"}}><span className="tag">Local GPU</span><span>5–10-second actions</span><span>60 FPS delivery · 24 FPS generation + RIFE</span><span className="muted">Projector playback unverified</span><Link to="/settings">Connection settings</Link></div>
    <div className="row small" style={{flexWrap:"wrap"}}>{["image","ltx","wan"].map(k=><span className="tag" key={k} title={capabilities[k]?.error}>{k}: {capabilities[k] ? capabilities[k].ready ? "dependencies ready" : capabilities[k].error : "checking dependencies…"}</span>)}</div>
    {busy && <div role="status" className="panel small">Working locally… Cold model loading may take several minutes. You can continue browsing the library.</div>}
    {error && <div role="alert" className="panel" style={{color:"var(--bad)",whiteSpace:"pre-wrap"}}>{error}</div>}
    <select aria-label="Generation project" value={p?.id || ""} onChange={e=>{setId(e.target.value);setMapping({});setProgramId("");setExportSet("");}}><option value="" disabled>Select an encounter</option>{projects.map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select>
    {!p && <div className="panel muted">Create an encounter, describe the creature, then approve its look before animation.</div>}
    {p && detail && <>
      <div className="split"><ProjectEditor key={`${p.id}-${p.revision}-${p.name}`} project={p} action={action} /><div className="panel col"><h3>1. Establish the creature</h3><span className="small muted">Save an appearance brief or ask the planner to suggest one. Generate alternatives, then select one character reference for every beat.</span><button disabled={busy || !p.character_design} onClick={()=>action(`/api/generation/projects/${p.id}/render`,{stage:"character"})}>Generate character candidate</button><span className="tag">{p.character_take_id ? "Character approved" : "Awaiting character approval"}</span><h3>2. Plan the encounter</h3><button disabled={busy || !p.concept} onClick={()=>action(`/api/generation/projects/${p.id}/plan`)}>Suggest storyboard from concept</button><button disabled={busy} onClick={()=>action(`/api/generation/projects/${p.id}/shots`,{name:`Beat ${detail.shots.length+1}`,prompt:"",sound:"",seconds:5,role:"action",surface:"A",handoff:"darkness"})}>Add a beat manually</button><span className="small muted">The planner adds editable beats. Approve each starting image before generating its video.</span></div></div>
      <h2 style={{margin:0}}>Storyboard</h2>
      {detail.shots.map(sh=><ShotEditor key={`${sh.id}-${sh.revision}-${sh.image_take_id}`} shot={sh} projectId={p.id} characterReady={!!p.character_take_id} action={action} />)}
      <h2 style={{margin:0}}>Review takes</h2>
      <div className="grid cards">{detail.takes.map(t=><div key={t.id} className="panel col"><div className="row"><b>{t.stage === "character" ? "Character candidate" : detail.shots.find(sh=>sh.id===t.shot_id)?.name}</b><span className="tag">{t.stage}</span>{t.provenance?.set_name && <span className="tag">{t.provenance.set_name} · Step {(t.provenance.sequence_index ?? 0)+1}</span>}<span className="tag">{t.status}</span></div>{!t.current && <span className="small" style={{color:"var(--warn)"}}>Earlier revision / changed reference</span>}{t.stage === "video" ? t.asset?.rendition_path ? <video src={fileUrl(t.asset.rendition_path)} controls style={{width:"100%",maxHeight:300,background:"black"}} /> : null : t.outputs[0] && <img src={t.outputs[0].url} alt="Generated character or starting image for approval" style={{width:"100%",maxHeight:300,objectFit:"contain",background:"black"}} />}{t.error && <div className="small" role="alert" style={{color:"var(--bad)"}}>{t.error}</div>}{t.provenance?.motion_prompt && <details><summary>Motion direction</summary><p className="small">{t.provenance.motion_prompt}</p></details>}<span className="small muted">{Math.round(t.elapsed_seconds)} seconds processing{t.approved ? " · Approved" : ""}</span><div className="row" style={{flexWrap:"wrap"}}><button disabled={!t.current || t.status!=="done"} onClick={()=>action(`/api/generation/takes/${t.id}/approve`)}>{t.approved ? "Select approved take" : t.stage === "video" ? "Approve motion and sound" : "Approve image"}</button>{t.stage === "video" && <button disabled={!t.current || !t.approved || t.status!=="done" || !!t.asset?.published} onClick={()=>action(`/api/generation/takes/${t.id}/publish`)}>{t.asset?.published ? "Published" : "Publish to library"}</button>}{["queued","running","processing"].includes(t.status) && <button onClick={()=>action(`/api/generation/takes/${t.id}/cancel`)}>Cancel</button>}{t.status === "error" && <button disabled={!t.current} onClick={()=>action(`/api/generation/takes/${t.id}/retry`)}>Retry this stage</button>}</div></div>)}</div>
      <div className="panel col"><h3>3. Place the encounter on the house</h3><label>Take set<select value={exportSet} onChange={e=>setExportSet(e.target.value)}><option value="">Original encounter</option>{Array.from(new Set(detail.takes.map(t=>t.provenance?.set_name).filter((s): s is string=>!!s))).map(s=><option key={s} value={s}>{s}</option>)}</select></label><span className="small muted">Map storyboard windows to layout surfaces. Every beat needs a current approved, published video.</span><div className="row" style={{flexWrap:"wrap"}}>{Array.from(new Set(detail.shots.map(sh=>sh.surface))).map(label=><label key={label}>Window {label}<select value={mapping[label] || ""} onChange={e=>setMapping({...mapping,[label]:e.target.value})}><option value="">Choose surface</option>{surfaces.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>)}</div><button className="primary" disabled={!detail.shots.length} onClick={exportEncounter}>Build encounter in Programs</button>{programId && <Link to="/programs">Open Programs to preview, adjust timing, and play</Link>}</div>
    </>}
  </div>;
}
