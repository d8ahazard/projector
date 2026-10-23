import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, Block, CompiledPreview, Cue, MarathonScheduler, MediaAsset, Program, ProgramItem, Schedule, Surface, uid } from "../api";
import { useLive } from "../live";
import { useFetch } from "../hooks";
import CueEditor, { PATTERNS, summarizeCue, TRANSITIONS } from "../components/CueEditor";
import EncounterEditor from "../components/EncounterEditor";
import ScenePicker from "../components/ScenePicker";
import HousePreview from "../components/HousePreview";
import Timeline from "../components/Timeline";
import MarathonEditor from "../components/MarathonEditor";
import ProjectorPreview from "../components/ProjectorPreview";

type Path = [number] | [number, number];

const newCue = (): Cue => ({
  type: "cue",
  id: uid(),
  name: "",
  clips: [],
  target: { mode: "all" },
  pattern: "together",
  transition: { type: "dip", duration: 0.8, easing: "ease-in-out" },
});

const newBlock = (): Block => ({ type: "block", id: uid(), name: "Block", repeat: 2, items: [] });
const newScheduler = (): MarathonScheduler => ({
  type: "scheduler", id: uid(), name: "After-dark marathon", roster: [], target: { mode: "all" },
  recycle_min: 1.5, recycle_max: 2, gap_min: 4, gap_max: 15,
  rotation_gap_min: 10, rotation_gap_max: 30, active_characters: 0, surface_buffer: 1,
  smart_choreography: true,
  fit: "contain", transition: { type: "dip", duration: 1.2, easing: "ease-in-out" },
});

function getAt(items: ProgramItem[], p: Path): ProgramItem | undefined {
  const top = items[p[0]];
  if (p.length === 1) return top;
  return top?.type === "block" ? top.items[p[1]] : undefined;
}

function removeAt(items: ProgramItem[], p: Path): [ProgramItem[], ProgramItem] {
  const out = items.map((it) => (it.type === "block" ? { ...it, items: [...it.items] } : it));
  if (p.length === 1) {
    const [x] = out.splice(p[0], 1);
    return [out, x];
  }
  const blk = out[p[0]] as Block;
  const [x] = blk.items.splice(p[1], 1);
  return [out, x];
}

function insertAt(items: ProgramItem[], p: Path, item: ProgramItem): ProgramItem[] {
  const out = items.map((it) => (it.type === "block" ? { ...it, items: [...it.items] } : it));
  if (p.length === 1 || item.type === "block" || item.type === "scheduler") out.splice(p[0], 0, item);
  else (out[p[0]] as Block).items.splice(p[1], 0, item as Cue);
  return out;
}

export default function ProgramsPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { toast, runner } = useLive();
  const { data: programs = [], reload } = useFetch<Program[]>("/api/programs", ["programs"], []);
  const { data: assets = [] } = useFetch<MediaAsset[]>("/api/media", ["media"], []);
  const { data: layout } = useFetch<{ surfaces: Surface[]; groups: string[] }>("/api/layout", ["layout"]);
  const surfaces = layout?.surfaces ?? [];
  const [prog, setProg] = useState<Program | null>(null);
  const [dirty, setDirty] = useState(false);
  const [selPath, setSelPath] = useState<Path | null>(null);
  const [tab, setTab] = useState<"queue" | "timeline">("queue");
  const [preview, setPreview] = useState<CompiledPreview | null>(null);
  const [cuePreview, setCuePreview] = useState<CompiledPreview | null>(null);
  const [showAutofill, setShowAutofill] = useState(false);
  const [showMixer, setShowMixer] = useState(false);
  const [preflight,setPreflight]=useState<{required_bytes:number;errors:string[];warnings:string[]}|null>(null);
  const dragFrom = useRef<Path | null>(null);
  const [dragOver, setDragOver] = useState<string>("");

  useEffect(() => {
    if (!id && programs.length) nav(`/programs/${programs[0].id}`, { replace: true });
  }, [id, programs, nav]);

  useEffect(() => {
    if (!id) return setProg(null);
    api.get<Program>(`/api/programs/${id}`).then((p) => {
      setProg(p);
      setDirty(false);
      setSelPath(null);
    });
  }, [id]);

  // Compile the unsaved program for the timeline and house preview.
  useEffect(() => {
    if (!prog) return;
    const t = window.setTimeout(() => {
      api
        .post<CompiledPreview>("/api/preview/program", { items: prog.items, loop_mode: prog.loop_mode, seed: prog.seed })
        .then(setPreview)
        .catch(() => setPreview(null));
    }, 350);
    return () => window.clearTimeout(t);
  }, [prog?.items, prog?.loop_mode, prog?.seed, surfaces.length, assets.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const selected = prog && selPath ? getAt(prog.items, selPath) : undefined;
  const selectedCue = selected && selected.type !== "block" && selected.type !== "scheduler" ? selected : undefined;
  const selectedScheduler = selected?.type === "scheduler" ? selected : undefined;

  useEffect(() => {
    if (!selectedCue) return setCuePreview(null);
    const t = window.setTimeout(() => {
      api.post<CompiledPreview>("/api/preview/cue", selectedCue).then(setCuePreview).catch(() => setCuePreview(null));
    }, 250);
    return () => window.clearTimeout(t);
  }, [selectedCue, surfaces.length]);

  const edit = (p: Partial<Program>) => {
    if (!prog) return;
    setProg({ ...prog, ...p });
    setDirty(true);
  };
  const setItems = (items: ProgramItem[]) => edit({ items });

  const updateAt = (p: Path, item: ProgramItem) => {
    if (!prog) return;
    const items = prog.items.map((it) => (it.type === "block" ? { ...it, items: [...it.items] } : it));
    if (p.length === 1) items[p[0]] = item;
    else (items[p[0]] as Block).items[p[1]] = item as Cue;
    setItems(items);
  };

  const save = async () => {
    if (!prog) return;
    try {
      const saved = await api.put<Program>(`/api/programs/${prog.id}`, { name: prog.name, loop_mode: prog.loop_mode, seed: prog.seed, items: prog.items });
      setProg(saved);
      setDirty(false);
      reload();
      toast(runner.program_id === prog.id ? "Saved; running show updated" : "Saved");
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const create = async () => {
    const p = await api.post<Program>("/api/programs", { name: "New program" });
    reload();
    nav(`/programs/${p.id}`);
  };

  const cueIdPath = (cueId: string): Path | null => {
    if (!prog) return null;
    for (let i = 0; i < prog.items.length; i++) {
      const it = prog.items[i];
      if (it.type !== "block" && it.id === cueId) return [i];
      if (it.type === "block") {
        const j = it.items.findIndex((c) => c.id === cueId);
        if (j >= 0) return [i, j];
      }
    }
    return null;
  };

  const onDrop = (to: Path) => {
    const from = dragFrom.current;
    dragFrom.current = null;
    setDragOver("");
    if (!prog || !from) return;
    const moving = getAt(prog.items, from);
    if (!moving) return;
    if ((moving.type === "block" || moving.type === "scheduler") && to.length > 1) return;
    let target: Path = to;
    // Adjust index if removing earlier sibling shifts the target.
    if (from.length === to.length && from[0] === to[0] && from.length === 2 && to.length === 2 && from[1] < to[1]) target = [to[0], to[1] - 1];
    else if (from.length === 1 && to.length >= 1 && from[0] < to[0]) target = (to.length === 1 ? [to[0] - 1] : [to[0] - 1, to[1]]) as Path;
    const [without, item] = removeAt(prog.items, from);
    setItems(insertAt(without, target, item));
    setSelPath(null);
  };

  const dnd = (p: Path) => ({
    draggable: true,
    onDragStart: (e: React.DragEvent) => {
      e.stopPropagation();
      dragFrom.current = p;
    },
    onDragOver: (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(p.join("."));
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      onDrop(p);
    },
  });

  const totalLabel = useMemo(() => {
    if (!preview) return "";
    const m = Math.round(preview.duration_ms / 60000);
    return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
  }, [preview]);
  const hasScheduler = !!prog?.items.some(item => item.type === "scheduler");

  const renderCue = (c: Cue, p: Path) => {
    const key = p.join(".");
    const tr = TRANSITIONS.find((t) => t.id === (c.transition?.type ?? "dip"))?.label;
    return (
      <div
        key={c.id}
        className={`queue-item ${selPath?.join(".") === key ? "selected" : ""} ${dragOver === key ? "dragover" : ""}`}
        onClick={(e) => {
          e.stopPropagation();
          setSelPath(p);
        }}
        {...dnd(p)}
      >
        <span className="muted">⠿</span>
        <div className="col grow" style={{ gap: 2, minWidth: 0 }}>
          <b>{c.name || PATTERNS.find((x) => x.id === c.pattern)?.label}</b>
          <span className="small muted" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {summarizeCue(c, assets, surfaces)}
          </span>
        </div>
        <span className="tag">{tr}</span>
        {(c.repeat ?? 1) > 1 && <span className="tag">×{c.repeat}</span>}
        {(c.hold ?? 0) > 0 && <span className="tag">hold {c.hold}s</span>}
        <button
          className="small"
          title="Duplicate"
          onClick={(e) => {
            e.stopPropagation();
            setItems(insertAt(prog!.items, (p.length === 1 ? [p[0] + 1] : [p[0], p[1] + 1]) as Path, { ...c, id: uid() }));
          }}
        >
          ⧉
        </button>
        <button
          className="small"
          title="Fire now on projectors"
          disabled={runner.state !== "playing"}
          onClick={(e) => {
            e.stopPropagation();
            api.post("/api/runner/oneshot", { cue: c, immediate: true }).catch((err) => toast(err.message, true));
          }}
        >
          ⚡
        </button>
        <button
          className="small"
          onClick={(e) => {
            e.stopPropagation();
            setItems(removeAt(prog!.items, p)[0]);
            setSelPath(null);
          }}
        >
          ✕
        </button>
      </div>
    );
  };

  const renderScheduler = (item: MarathonScheduler, p: Path) => {
    const key = p.join(".");
    const clips = item.roster.reduce((n, c) => n + c.clips.length, 0);
    return <div key={item.id} className={`queue-item marathon-item ${selPath?.join(".") === key ? "selected" : ""} ${dragOver === key ? "dragover" : ""}`} onClick={e => { e.stopPropagation(); setSelPath(p); }} {...dnd(p)}>
      <span className="marathon-orbit">∞</span>
      <div className="col grow" style={{ gap: 2, minWidth: 0 }}><b>{item.name}</b><span className="small muted">{item.roster.length} characters · {clips} clips · {item.target.mode === "all" ? "every surface" : `${item.target.surfaces?.length ?? 0} surfaces`} · {item.gap_min}–{item.gap_max}s darkness</span></div>
      <span className="tag">{item.recycle_min}–{item.recycle_max}× recycle</span>
      <button className="small" onClick={e => { e.stopPropagation(); setItems(removeAt(prog!.items, p)[0]); setSelPath(null); }}>✕</button>
    </div>;
  };

  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="row">
        <h1 style={{ margin: 0 }}>Programs</h1>
        <select value={id ?? ""} onChange={(e) => nav(`/programs/${e.target.value}`)}>
          {programs.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <button onClick={create}>New</button>
        <button onClick={() => setShowAutofill(true)}>Auto-fill…</button>
        <button onClick={() => setShowMixer(true)}>Mix shows…</button>
        {prog && (
          <>
            <button
              onClick={async () => {
                const c = await api.post<Program>(`/api/programs/${prog.id}/duplicate`);
                reload();
                nav(`/programs/${c.id}`);
              }}
            >
              Duplicate
            </button>
            <button
              className="danger"
              onClick={async () => {
                if (!confirm(`Delete program "${prog.name}"?`)) return;
                await api.del(`/api/programs/${prog.id}`);
                reload();
                nav("/programs");
              }}
            >
              Delete
            </button>
          </>
        )}
      </div>

      {showAutofill && (
        <AutofillDialog
          assets={assets}
          programId={prog?.id}
          onClose={() => setShowAutofill(false)}
          onDone={(p) => {
            setShowAutofill(false);
            reload();
            if (p.id === prog?.id) {
              setProg(p);
              setDirty(false);
            } else nav(`/programs/${p.id}`);
          }}
        />
      )}
      {showMixer && <MixShowsDialog programs={programs} onClose={() => setShowMixer(false)} onDone={p => { setShowMixer(false); reload(); nav(`/programs/${p.id}`); }} />}

      {!prog ? (
        <div className="panel muted">Create a program to start choreographing.</div>
      ) : (
        <>
          <div className="panel row program-toolbar">
            <input value={prog.name} onChange={(e) => edit({ name: e.target.value })} style={{ fontWeight: 600, minWidth: 220 }} />
            <label className="inline">
              Loop
              <select value={prog.loop_mode} onChange={(e) => edit({ loop_mode: e.target.value as Program["loop_mode"] })}>
                <option value="loop">Loop forever</option>
                <option value="shuffle_blocks">Shuffle items, loop</option>
                <option value="once">Play once</option>
              </select>
            </label>
            <label className="inline" title={hasScheduler ? "Used for the editor preview; live marathon runs receive a fresh seed" : "Random choices are reproducible for a given seed"}>
              {hasScheduler ? "Preview seed" : "Seed"}
              <input type="number" style={{ width: 70 }} value={prog.seed} onChange={(e) => edit({ seed: Number(e.target.value) })} />
            </label>
            <span className="muted small">{totalLabel && (hasScheduler ? `Continuous · sample rotation ≈ ${totalLabel} · fresh each start` : `One pass ≈ ${totalLabel}`)}</span>
            <button className="primary right" disabled={!dirty} onClick={save}>
              {dirty ? "Save" : "Saved"}
            </button>
            <button
              onClick={async () => {
                if (dirty) await save();
                api.post("/api/runner/start", { program_id: prog.id }).catch((e) => toast(e.message, true));
              }}
            >
              ▶ Play on projectors
            </button>
            <button onClick={async()=>{try{if(dirty)await save();setPreflight(await api.get(`/api/programs/${prog.id}/preflight`));}catch(e){toast((e as Error).message,true);}}}>Check playback & storage</button>
            <button title="Add every distinct eligible catalogue action; target and orientation duplicates remain selectable variants" onClick={async()=>{try{if(dirty)await save();await api.post(`/api/programs/${prog.id}/sync-catalog`,{});toast("Catalogue sync started. The completed roster replaces the current roster atomically.");}catch(e){toast((e as Error).message,true);}}}>Sync complete catalogue</button>
          </div>
          {preflight && <div className="panel col small"><b>{(preflight.required_bytes/1024**2).toFixed(0)} MB of unique media per player</b>{preflight.errors.map(x=><span key={x} style={{color:"var(--bad)"}}>{x}</span>)}{preflight.warnings.map(x=><span key={x} style={{color:"var(--warn)"}}>{x}</span>)}</div>}

          <div className="panel">
            <HousePreview preview={selectedCue ? cuePreview : preview} surfaces={surfaces} assets={assets} />
            <div className="small muted" style={{ marginTop: 4 }}>
              {selectedCue ? "Previewing the selected cue." : hasScheduler ? "Previewing one sample character rotation. The live marathon continues indefinitely." : "Previewing the whole program (one pass)."} Thumbnails stand in for video.
            </div>
            {(selectedCue || selectedScheduler) && <ProjectorPreview cue={(selectedCue || selectedScheduler)!} label="Play selected set on projector" />}
          </div>

          <div className="split">
            <div className="col">
              <div className="tabs">
                <button className={tab === "queue" ? "active" : ""} onClick={() => setTab("queue")}>
                  Queue
                </button>
                <button className={tab === "timeline" ? "active" : ""} onClick={() => setTab("timeline")}>
                  Timeline
                </button>
                <span className="right row">
                  <button
                    onClick={() => {
                      const c = newCue();
                      setItems([...prog.items, c]);
                      setSelPath([prog.items.length]);
                    }}
                  >
                    + Cue
                  </button>
                  <button onClick={() => setItems([...prog.items, newBlock()])}>+ Block</button>
                  <button onClick={()=>{const c:Cue={...newCue(),type:"encounter",name:"Window encounter",tracks:[]};setItems([...prog.items,c]);setSelPath([prog.items.length]);}}>+ Encounter</button>
                  <button onClick={async()=>{try{const smart=await api.get<{roster:MarathonScheduler["roster"];groups:NonNullable<MarathonScheduler["groups"]>;segments:number}>("/api/library/movement/roster");if(!smart.segments){toast("Prepare approved beats in Movement Lab first",true);return}const item={...newScheduler(),id:uid(),name:"Smart window choreography",roster:smart.roster,groups:smart.groups,smart_choreography:true,gap_min:1,gap_max:6,rotation_gap_min:6,rotation_gap_max:18};setItems([...prog.items,item]);setSelPath([prog.items.length]);}catch(e){toast((e as Error).message,true)}}}>+ Smart movement</button>
                  <button className="primary" onClick={()=>{const item=newScheduler();setItems([...prog.items,item]);setSelPath([prog.items.length]);}}>+ Marathon scheduler</button>
                </span>
              </div>

              {tab === "queue" ? (
                <div className="col" onClick={() => setSelPath(null)}>
                  {prog.items.length === 0 && <div className="muted panel">Empty. Add a cue, or use Auto-fill to generate hours of variety.</div>}
                  {prog.items.map((it, i) =>
                    it.type === "block" ? (
                      <div
                        key={it.id}
                        className={`block ${dragOver === String(i) ? "dragover" : ""}`}
                        {...dnd([i])}
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelPath([i]);
                        }}
                        style={selPath?.join(".") === String(i) ? { borderColor: "var(--accent)" } : undefined}
                      >
                        <div className="block-head">
                          <span className="muted">⠿</span>
                          <input
                            value={it.name}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => updateAt([i], { ...it, name: e.target.value })}
                            style={{ fontWeight: 600 }}
                          />
                          <label className="inline small">
                            repeat
                            <input type="number" min={1} style={{ width: 60 }} value={it.repeat} onChange={(e) => updateAt([i], { ...it, repeat: Number(e.target.value) })} />
                          </label>
                          <label className="inline small">
                            <input type="checkbox" checked={!!it.shuffle} onChange={(e) => updateAt([i], { ...it, shuffle: e.target.checked })} /> shuffle
                          </label>
                          <button
                            className="small right"
                            onClick={(e) => {
                              e.stopPropagation();
                              const c = newCue();
                              updateAt([i], { ...it, items: [...it.items, c] });
                              setSelPath([i, it.items.length]);
                            }}
                          >
                            + Cue
                          </button>
                          <button
                            className="small"
                            onClick={(e) => {
                              e.stopPropagation();
                              setItems(removeAt(prog.items, [i])[0]);
                            }}
                          >
                            ✕
                          </button>
                        </div>
                        {it.items.map((c, j) => renderCue(c, [i, j]))}
                        <div
                          className="small muted"
                          style={{ padding: 6, border: dragOver === `${i}.end` ? "1px dashed var(--accent2)" : "1px dashed transparent", borderRadius: 6 }}
                          onDragOver={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            setDragOver(`${i}.end`);
                          }}
                          onDrop={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            onDrop([i, it.items.length]);
                          }}
                        >
                          drop cues here
                        </div>
                      </div>
                    ) : it.type === "scheduler" ? (
                      renderScheduler(it, [i])
                    ) : (
                      renderCue(it, [i])
                    ),
                  )}
                  <div
                    className="small muted"
                    style={{ padding: 8, border: dragOver === "end" ? "1px dashed var(--accent2)" : "1px dashed transparent", borderRadius: 6 }}
                    onDragOver={(e) => {
                      e.preventDefault();
                      setDragOver("end");
                    }}
                    onDrop={(e) => {
                      e.preventDefault();
                      onDrop([prog.items.length]);
                    }}
                  >
                    drop here to move to the end
                  </div>
                </div>
              ) : (
                <div className="panel">
                  <Timeline
                    preview={preview}
                    surfaces={surfaces}
                    assets={assets}
                    selectedCue={selectedCue?.id ?? null}
                    onSelect={(cid) => setSelPath(cueIdPath(cid))}
                    onOffset={(cid, sid, delta) => {
                      const p = cueIdPath(cid);
                      if (!p) return;
                      const c = getAt(prog.items, p) as Cue;
                      const offsets = { ...(c.offsets ?? {}) };
                      offsets[sid] = Math.round(((offsets[sid] ?? 0) + delta) * 100) / 100;
                      updateAt(p, { ...c, offsets });
                    }}
                  />
                </div>
              )}

              <Schedules programId={prog.id} programs={programs} />
            </div>

            <div className="panel" style={{ alignSelf: "flex-start", position: "sticky", top: 12, maxHeight: "calc(100vh - 24px)", overflowY: "auto" }}>
              {selectedScheduler && selPath ? (
                <MarathonEditor item={selectedScheduler} assets={assets} surfaces={surfaces} onChange={item=>updateAt(selPath,item)} />
              ) : selectedCue && selPath ? (
                selectedCue.type === "encounter" ? <EncounterEditor cue={selectedCue} assets={assets} surfaces={surfaces} onChange={c=>updateAt(selPath,c)} /> : <CueEditor cue={selectedCue} assets={assets} surfaces={surfaces} groups={layout?.groups ?? []} onChange={(c) => updateAt(selPath, c)} />
              ) : (
                <div className="muted small col">
                  <b>Cue editor</b>
                  <span>Select a cue to edit what plays, where, and how it transitions.</span>
                  <span>Blocks group cues that loop together; drag cues in and out of blocks.</span>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function MixShowsDialog({ programs, onClose, onDone }: { programs: Program[]; onClose: () => void; onDone: (p: Program) => void }) {
  const { toast } = useLive();
  const [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState("After-dark everything mix");
  const [mode, setMode] = useState<"characters" | "shows">("characters");
  const [busy, setBusy] = useState(false);
  const toggle = (id: string) => setSelected(ids => ids.includes(id) ? ids.filter(x => x !== id) : [...ids, id]);
  const create = async () => {
    setBusy(true);
    try {
      const mixed = await api.post<Program>("/api/programs/mix", { name, program_ids: selected, mode });
      toast(mode === "characters" ? "Combined character marathon created" : "Shuffled show rotation created");
      onDone(mixed);
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return <div className="modal-backdrop" onClick={onClose}><div className="modal panel col" onClick={e => e.stopPropagation()}>
    <div className="row"><div className="grow"><div className="eyebrow">MASTER SCHEDULER</div><h2 style={{margin:0}}>Mix shows</h2></div><button onClick={onClose}>✕</button></div>
    <p className="muted">Choose the source shows once. The result is a normal editable Program you can preview, schedule, and tune independently.</p>
    <label>Name<input value={name} onChange={e => setName(e.target.value)} /></label>
    <div className="panel col">
      <label className="inline"><input type="radio" checked={mode === "characters"} onChange={() => setMode("characters")} /><span><b>Mix related families</b><br/><span className="small muted">Merge the rosters while preserving each show’s scene families. Related characters can overlap; the scheduler finishes a family before changing themes.</span></span></label>
      <label className="inline"><input type="radio" checked={mode === "shows"} onChange={() => setMode("shows")} /><span><b>Shuffle complete sets</b><br/><span className="small muted">Keep each show’s existing cues and schedulers, but randomize which show/set runs next. Every selected show cycles before reuse.</span></span></label>
    </div>
    <div className="surface-checks">
      {programs.map(p => <label key={p.id} className="inline"><input type="checkbox" checked={selected.includes(p.id)} onChange={() => toggle(p.id)} />{p.name}</label>)}
    </div>
    <div className="row"><span className="muted small grow">{selected.length} selected · choose at least two</span><button onClick={onClose}>Cancel</button><button className="primary" disabled={busy || selected.length < 2 || !name.trim()} onClick={create}>{busy ? "Building…" : "Create mix"}</button></div>
  </div></div>;
}

function AutofillDialog({ assets, programId, onClose, onDone }: { assets: MediaAsset[]; programId?: string; onClose: () => void; onDone: (p: Program) => void }) {
  const [assetIds,setAssetIds]=useState<string[]>([]);
  const { toast } = useLive();
  const allTags = useMemo(() => Array.from(new Set(assets.flatMap((a) => a.tags))).sort(), [assets]);
  const [name, setName] = useState("Halloween night");
  const [hours, setHours] = useState(4);
  const [season, setSeason] = useState("halloween");
  const [tags, setTags] = useState<string[]>([]);
  const [patterns, setPatterns] = useState<string[]>(["together", "each", "sequence", "random-one", "span"]);
  const [transitions, setTransitions] = useState<string[]>(["dip", "crossfade", "cascade", "wipe"]);
  const [append, setAppend] = useState(false);
  const toggle = (list: string[], v: string, set: (l: string[]) => void) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  const run = async () => {
    try {
      const p = await api.post<Program>("/api/programs/autofill", {
        name,
        hours,
        season,
        tags,
        patterns,
        transitions,
        seed: Math.floor(Math.random() * 100000),
        program_id: append ? programId : undefined,
        asset_ids: assetIds,
      });
      toast(`Generated ${p.items.length} cues`);
      onDone(p);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  return (
    <div className="panel col" style={{ borderColor: "var(--accent)" }}>
      <div className="row">
        <b>Auto-fill</b>
        <span className="muted small">Builds a long, varied program from your library.</span>
        <button className="small right" onClick={onClose}>
          ✕
        </button>
      </div>
      <details><summary>Choose exact AtmosFX variants ({assetIds.length} selected)</summary><ScenePicker onSelect={id=>setAssetIds(ids=>Array.from(new Set([...ids,id])))} /><button onClick={()=>setAssetIds([])}>Use all matching prepared clips</button></details>
      <div className="field-row">
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={append} />
        </label>
        <label>
          Hours
          <input type="number" min={0.1} step={0.5} value={hours} onChange={(e) => setHours(Number(e.target.value))} />
        </label>
        <label>
          Season
          <select value={season} onChange={(e) => setSeason(e.target.value)}>
            <option value="halloween">Halloween</option>
            <option value="christmas">Christmas</option>
            <option value="">Any</option>
          </select>
        </label>
      </div>
      <div className="row small">
        <span className="muted">Tags:</span>
        {allTags.length === 0 && <span className="muted">none yet</span>}
        {allTags.map((t) => (
          <label key={t} className="inline">
            <input type="checkbox" checked={tags.includes(t)} onChange={() => toggle(tags, t, setTags)} />
            {t}
          </label>
        ))}
      </div>
      <div className="row small">
        <span className="muted">Patterns:</span>
        {PATTERNS.map((p) => (
          <label key={p.id} className="inline">
            <input type="checkbox" checked={patterns.includes(p.id)} onChange={() => toggle(patterns, p.id, setPatterns)} />
            {p.label}
          </label>
        ))}
      </div>
      <div className="row small">
        <span className="muted">Transitions:</span>
        {TRANSITIONS.map((t) => (
          <label key={t.id} className="inline">
            <input type="checkbox" checked={transitions.includes(t.id)} onChange={() => toggle(transitions, t.id, setTransitions)} />
            {t.label}
          </label>
        ))}
      </div>
      <div className="row">
        {programId && (
          <label className="inline">
            <input type="checkbox" checked={append} onChange={(e) => setAppend(e.target.checked)} /> Append to current program
          </label>
        )}
        <button className="primary right" onClick={run}>
          Generate
        </button>
      </div>
    </div>
  );
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function Schedules({ programId, programs }: { programId: string; programs: Program[] }) {
  const { toast } = useLive();
  const { data } = useFetch<{ schedules: Schedule[]; idle_program_id: string | null }>("/api/schedules", ["schedules"]);
  const mine = (data?.schedules ?? []).filter((s) => s.program_id === programId);
  const patch = (s: Schedule, p: Partial<Schedule>) => api.patch(`/api/schedules/${s.id}`, p).catch((e) => toast(e.message, true));

  return (
    <div className="panel col">
      <div className="row">
        <h3 style={{ margin: 0 }}>Schedule</h3>
        <span className="muted small">Run this program automatically during these windows.</span>
        <button className="small right" onClick={() => api.post("/api/schedules", { program_id: programId, start: "18:00", end: "23:00" })}>
          + Time window
        </button>
      </div>
      {mine.map((s) => (
        <div key={s.id} className="row small">
          <label className="inline">
            <input type="checkbox" checked={s.enabled} onChange={(e) => patch(s, { enabled: e.target.checked })} /> on
          </label>
          {DAYS.map((d, i) => (
            <label key={d} className="inline">
              <input
                type="checkbox"
                checked={s.days.includes(i)}
                onChange={(e) => patch(s, { days: e.target.checked ? [...s.days, i].sort() : s.days.filter((x) => x !== i) })}
              />
              {d}
            </label>
          ))}
          <input type="time" value={s.start} onChange={(e) => patch(s, { start: e.target.value })} />
          to
          <input type="time" value={s.end} onChange={(e) => patch(s, { end: e.target.value })} />
          <button className="small right" onClick={() => api.del(`/api/schedules/${s.id}`)}>
            ✕
          </button>
        </div>
      ))}
      <label className="inline small">
        Outside scheduled windows, play
        <select
          value={data?.idle_program_id ?? ""}
          onChange={(e) => api.put("/api/schedules/idle", { program_id: e.target.value || null }).catch((err) => toast(err.message, true))}
        >
          <option value="">nothing (projectors black)</option>
          {programs.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
