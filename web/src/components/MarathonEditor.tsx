import { fileUrl, MarathonScheduler, MediaAsset, Surface, uid } from "../api";

export default function MarathonEditor({ item, assets, surfaces, onChange }: {
  item: MarathonScheduler;
  assets: MediaAsset[];
  surfaces: Surface[];
  onChange: (item: MarathonScheduler) => void;
}) {
  const ready = assets.filter(a => a.kind === "video" && a.status === "ready");
  const patch = (p: Partial<MarathonScheduler>) => onChange({ ...item, ...p });
  const patchCharacter = (index: number, value: Partial<MarathonScheduler["roster"][number]>) => {
    const roster = item.roster.map((c, i) => i === index ? { ...c, ...value } : c);
    patch({ roster });
  };
  const selected = item.target.mode === "list" ? item.target.surfaces ?? [] : [];
  const configuredFamilies = item.groups ?? [];
  const assigned = new Set(configuredFamilies.flatMap(family => family.character_ids));
  const families = [
    ...configuredFamilies,
    ...(item.roster.some(character => !assigned.has(character.id)) ? [{
      id: "unassigned", name: configuredFamilies.length ? "Unassigned" : "All characters",
      character_ids: item.roster.filter(character => !assigned.has(character.id)).map(character => character.id),
    }] : []),
  ];
  const toggleSurface = (id: string) => patch({
    target: { mode: "list", surfaces: selected.includes(id) ? selected.filter(x => x !== id) : [...selected, id] },
  });

  return <div className="col marathon-editor">
    <div>
      <h3>Marathon scheduler</h3>
      <p className="small muted">Builds a fresh shared queue each run. It cycles every clip before reuse, moves characters across available surfaces, and uses short randomized pauses between appearances. Increase the darkness controls only when you want a slower, emptier haunt.</p>
    </div>
    <label>Scheduler name<input value={item.name} onChange={e => patch({ name: e.target.value })} /></label>
    <label className="setup-consent"><input type="checkbox" checked={!!item.smart_choreography} onChange={e => patch({ smart_choreography: e.target.checked })} /><span><b>Smart movement chaining</b><br /><span className="small muted">Prefer approved beats whose exit and entrance directions continue naturally across adjacent calibrated surfaces.</span></span></label>
    <div className="field-row">
      <label>Recycle from<input type="number" min="0.25" max="10" step="0.1" value={item.recycle_min} onChange={e => patch({ recycle_min: Number(e.target.value) })} /></label>
      <label>Recycle to<input type="number" min="0.25" max="10" step="0.1" value={item.recycle_max} onChange={e => patch({ recycle_max: Number(e.target.value) })} /></label>
      <label>Darkness min (sec)<input type="number" min="0" max="3600" value={item.gap_min} onChange={e => patch({ gap_min: Number(e.target.value) })} /></label>
      <label>Darkness max (sec)<input type="number" min="0" max="3600" value={item.gap_max} onChange={e => patch({ gap_max: Number(e.target.value) })} /></label>
      <label>Character break min<input type="number" min="0" max="7200" value={item.rotation_gap_min} onChange={e => patch({ rotation_gap_min: Number(e.target.value) })} /></label>
      <label>Character break max<input type="number" min="0" max="7200" value={item.rotation_gap_max} onChange={e => patch({ rotation_gap_max: Number(e.target.value) })} /></label>
      <label>Active characters<input type="number" min="0" max="64" value={item.active_characters ?? 0} onChange={e => patch({ active_characters: Number(e.target.value) })} /><span>0 uses one per available surface</span></label>
      <label>Surface buffer (sec)<input type="number" min="0" max="60" step="0.5" value={item.surface_buffer ?? 2} onChange={e => patch({ surface_buffer: Number(e.target.value) })} /></label>
    </div>
    <div className="panel col">
      <b>Projection surfaces</b>
      <label className="inline"><input type="radio" checked={item.target.mode === "all"} onChange={() => patch({ target: { mode: "all" } })} />All current and future surfaces</label>
      <label className="inline"><input type="radio" checked={item.target.mode === "list"} onChange={() => patch({ target: { mode: "list", surfaces: selected.length ? selected : surfaces.map(s => s.id) } })} />Only selected surfaces</label>
      {item.target.mode === "list" && <div className="surface-checks">{surfaces.map(s => <label className="inline" key={s.id}><input type="checkbox" checked={selected.includes(s.id)} onChange={() => toggleSurface(s.id)} />{s.name}</label>)}</div>}
    </div>
    <div className="col">
      <div>
        <b>Families</b>
        <p className="small muted">The scheduler finishes one family before moving to another. Cards show the exact characters and distinct actions that may share a scene.</p>
      </div>
      <div className="family-gallery">
        {families.map(family => {
          const characters = item.roster.filter(character => family.character_ids.includes(character.id));
          const clips = Array.from(new Set(characters.flatMap(character => character.clips)));
          return <div className="family-card" key={family.id}>
            <div className="family-card-head">
              <div><div className="eyebrow">SCENE FAMILY</div><b>{family.name}</b></div>
              <span className="tag">{characters.length} character{characters.length === 1 ? "" : "s"} · {clips.length} action{clips.length === 1 ? "" : "s"}</span>
            </div>
            <div className="family-thumbs">
              {clips.slice(0, 8).map(id => {
                const asset = assets.find(candidate => candidate.id === id);
                return <div className="family-thumb" key={id} title={asset?.title ?? id} style={{ backgroundImage: asset?.thumb_path ? `url(${fileUrl(asset.thumb_path)})` : undefined }}>
                  <span>{asset?.title ?? id}</span>
                </div>;
              })}
            </div>
            <div className="clip-chip-list">{characters.map(character => <span className="clip-chip" key={character.id}>{character.name} · {character.clips.length}</span>)}</div>
          </div>;
        })}
      </div>
      {!families.length && <span className="small muted">Add characters to create a family gallery.</span>}
    </div>
    <div className="col">
      <div className="row"><b>Character roster</b><button className="small right" onClick={() => patch({ roster: [...item.roster, { id: uid(), name: `Character ${item.roster.length + 1}`, clips: [] }] })}>+ Character</button></div>
      {item.roster.map((character, i) => <div className="panel col" key={character.id}>
        <div className="row"><input className="grow" value={character.name} onChange={e => patchCharacter(i, { name: e.target.value })} /><button className="small danger" onClick={() => patch({ roster: item.roster.filter((_, j) => i !== j) })}>Remove</button></div>
        <div className="clip-chip-list">{character.clips.map(id => {
          const asset = assets.find(a => a.id === id);
          return <button className="clip-chip" key={id} title="Remove clip" onClick={() => patchCharacter(i, { clips: character.clips.filter(x => x !== id) })}>{asset?.title ?? id} ×</button>;
        })}</div>
        <select value="" onChange={e => e.target.value && patchCharacter(i, { clips: [...character.clips, e.target.value] })}>
          <option value="">Add prepared clip…</option>
          {ready.filter(a => !character.clips.includes(a.id)).map(a => <option value={a.id} key={a.id}>{a.title}</option>)}
        </select>
      </div>)}
      {!item.roster.length && <span className="small muted">Add at least one character, then assign its prepared clip variants.</span>}
    </div>
  </div>;
}
