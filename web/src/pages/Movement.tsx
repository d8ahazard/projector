import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useLive } from "../live";

type Segment = {
  id:string; source_file_id:string; segment_index:number; start_ms:number; end_ms:number;
  active_start_ms:number; active_end_ms:number; enter_edge:string; exit_edge:string; dx:number; dy:number;
  confidence:number; approval:string; character:string; family:string; narrative_group:string; mirror_safe:boolean;
  asset_id?:string|null; asset_status:string; asset_error?:string;
  source?:{title:string; technical:{duration?:number}; thumb_url?:string; relative_path:string};
};
type Page = {items:Segment[];total:number;page:number};

const sec=(ms:number)=>(ms/1000).toFixed(1);
const arrow=(s:Segment)=>s.dx>.12?"→":s.dx<-.12?"←":"•";

export default function MovementPage(){
  const {toast}=useLive();
  const [data,setData]=useState<Page>({items:[],total:0,page:1});
  const [q,setQ]=useState(""); const [approval,setApproval]=useState(""); const [page,setPage]=useState(1);
  const [busy,setBusy]=useState(false); const [selected,setSelected]=useState<Segment[]>([]);
  const aRef=useRef<HTMLVideoElement>(null); const bRef=useRef<HTMLVideoElement>(null);
  const load=async()=>setData(await api.get(`/api/library/movement/segments?page=${page}&limit=24&q=${encodeURIComponent(q)}&approval=${approval}`));
  useEffect(()=>{void load()},[page,approval]); // eslint-disable-line react-hooks/exhaustive-deps
  const families=useMemo(()=>Array.from(new Set(data.items.map(x=>x.family))),[data.items]);
  const act=async(path:string,body?:unknown,method:"post"|"patch"|"put"="post")=>{try{setBusy(true);await api[method](path,body);await load()}catch(e){toast((e as Error).message,true)}finally{setBusy(false)}};
  const choose=(s:Segment)=>setSelected(old=>old.some(x=>x.id===s.id)?old.filter(x=>x.id!==s.id):[...old.slice(-1),s]);
  const playPair=()=>{if(!aRef.current||!bRef.current||selected.length<2)return;bRef.current.pause();bRef.current.currentTime=selected[1].start_ms/1000;aRef.current.currentTime=selected[0].start_ms/1000;aRef.current.play();};
  useEffect(()=>{const a=aRef.current;if(!a)return;const tick=()=>{if(selected[0]&&a.currentTime>=selected[0].end_ms/1000){a.pause();if(bRef.current&&selected[1]){bRef.current.currentTime=selected[1].start_ms/1000;void bRef.current.play()}}};a.addEventListener("timeupdate",tick);return()=>a.removeEventListener("timeupdate",tick)},[selected]);
  return <div className="col movement-lab" style={{gap:18}}>
    <div className="page-heading"><div><span className="eyebrow">SMART CHOREOGRAPHY</span><h1>Movement Lab</h1><p>Find entrances, exits, crossings, and reusable actions inside your source scenes.</p></div><div className="row"><button disabled={busy} onClick={()=>act("/api/library/movement/prepare-approved",{})}>Prepare approved</button><button className="primary" disabled={busy} onClick={()=>act("/api/library/motion",{})}>Analyze library</button></div></div>
    <div className="panel row program-toolbar"><input className="grow" placeholder="Character, collection, or action" value={q} onChange={e=>setQ(e.target.value)} onKeyDown={e=>e.key==="Enter"&&(setPage(1),void load())}/><select value={approval} onChange={e=>{setApproval(e.target.value);setPage(1)}}><option value="">All decisions</option><option value="auto">Auto-approved</option><option value="approved">Approved</option><option value="review">Needs review</option><option value="rejected">Rejected</option></select><button onClick={()=>{setPage(1);void load()}}>Search</button><span className="muted small">{data.total} detected beats · {families.length} families on this page</span></div>
    {selected.length>0&&<section className="panel col handoff-preview"><div className="row"><div><div className="eyebrow">A → B PREVIEW</div><b>{selected.map(x=>x.character||x.source?.title).join(" → ")}</b></div><button className="right primary" disabled={selected.length<2} onClick={playPair}>Play handoff</button>{selected.length===2&&<button onClick={()=>act(`/api/library/movement/links/${selected[0].id}/${selected[1].id}`,{decision:"force",relation:selected[0].character===selected[1].character?"same_character":"narrative"},"put")}>Link these beats</button>}<button onClick={()=>setSelected([])}>Clear</button></div><div className="movement-preview-grid">{selected.map((s,i)=><div key={s.id}><span className="tag">{i?"B":"A"} · {s.enter_edge} {arrow(s)} {s.exit_edge}</span><video ref={i===0?aRef:bRef} src={`/files/source/${s.source_file_id}#t=${s.start_ms/1000},${s.end_ms/1000}`} preload="metadata" controls /></div>)}</div></section>}
    <div className="movement-grid">{data.items.map(s=>{const duration=Math.max(1,(s.source?.technical.duration??s.end_ms/1000)*1000);const left=s.start_ms/duration*100;const width=(s.end_ms-s.start_ms)/duration*100;return <article className={`movement-card ${selected.some(x=>x.id===s.id)?"selected":""}`} key={s.id}>
      <button className="movement-image" onClick={()=>choose(s)} style={{backgroundImage:s.source?.thumb_url?`url(${s.source.thumb_url})`:undefined}}><span>{arrow(s)}</span></button>
      <div className="col movement-body"><div className="row"><div className="grow"><div className="eyebrow">{s.family}</div><b>{s.character||s.source?.title}</b><div className="small muted">{s.narrative_group}</div></div><span className={`status-pill ${s.approval==="auto"||s.approval==="approved"?"online":""}`}>{s.approval}</span></div>
      <div className="movement-timeline"><i style={{left:`${left}%`,width:`${Math.max(2,width)}%`}} /></div>
      <div className="row small"><span>{sec(s.start_ms)}–{sec(s.end_ms)}s</span><span className="tag">{s.enter_edge} {arrow(s)} {s.exit_edge}</span><span className="right">{Math.round(s.confidence*100)}%</span></div>
      <div className="row"><button disabled={busy} onClick={()=>act(`/api/library/movement/segments/${s.id}`,{approval:"approved"},"patch")}>Approve</button><button disabled={busy} onClick={()=>act(`/api/library/movement/segments/${s.id}`,{approval:"rejected"},"patch")}>Reject</button><label className="inline small"><input type="checkbox" checked={s.mirror_safe} onChange={e=>act(`/api/library/movement/segments/${s.id}`,{mirror_safe:e.target.checked},"patch")}/>Mirror safe</label></div>
      <div className="row"><button className="primary grow" disabled={busy||!["auto","approved"].includes(s.approval)} onClick={()=>act(`/api/library/movement/segments/${s.id}/prepare`)}>{s.asset_status==="ready"?"Prepared":s.asset_status==="processing"?"Preparing…":"Prepare beat"}</button><button onClick={()=>choose(s)}>{selected.some(x=>x.id===s.id)?"Selected":"Add to A/B"}</button></div>{s.asset_error&&<span className="small" style={{color:"var(--bad)"}}>{s.asset_error}</span>}
      </div></article>})}</div>
    {!data.items.length&&<div className="panel muted">No movement analysis yet. Run Analyze library, or analyze selected sources from the catalogue.</div>}
    <div className="row"><button disabled={page<=1} onClick={()=>setPage(page-1)}>Previous</button><span>Page {page}</span><button disabled={page*24>=data.total} onClick={()=>setPage(page+1)}>Next</button></div>
  </div>
}
