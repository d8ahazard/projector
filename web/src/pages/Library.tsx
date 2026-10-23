import { useState } from "react";
import { api, fileUrl, fmtDuration, MediaAsset, Preset, SourcePack } from "../api";
import { useLive } from "../live";
import { useFetch } from "../hooks";
import ScenePicker from "../components/ScenePicker";
import ProjectorPreview from "../components/ProjectorPreview";
import { useNavigate } from "react-router-dom";

export default function LibraryPage() {
  const [tab, setTab] = useState("packs");
  return <div className="col" style={{ gap: 18 }}><div className="row"><h1 className="grow">Library</h1><button className={tab === "packs" ? "primary" : ""} onClick={() => setTab("packs")}>Halloween packs</button><button className={tab === "scenes" ? "primary" : ""} onClick={() => setTab("scenes")}>Scene catalogue</button><button className={tab === "assets" ? "primary" : ""} onClick={() => setTab("assets")}>Prepared clips</button></div>{tab === "packs" ? <PackLibrary /> : tab === "scenes" ? <ScenePicker /> : <AssetLibrary />}</div>;
}

type PackImportResult = { id: string; name: string; queued: number; skipped: number; errors: string[]; scan_job_id: string };

function PackLibrary() {
  const { toast } = useLive();
  const navigate = useNavigate();
  const { data: packs = [] } = useFetch<SourcePack[]>("/api/library/packs", ["library", "media"], []);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const importFiles = async (files: FileList | null) => {
    if (!files?.length || !name.trim()) { toast("Name the package and choose its folder or ZIP", true); return; }
    const form = new FormData();
    Array.from(files).forEach(file => {
      const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      form.append("files", file, relative);
    });
    form.append("pack_name", name.trim());
    setBusy(true);
    try {
      const result = await api.post<PackImportResult>("/api/library/packs/import", form);
      toast(`${result.name}: ${result.queued} new video${result.queued === 1 ? "" : "s"} queued${result.skipped ? `, ${result.skipped} already present` : ""}.`);
      setName("");
    } catch (error) { toast((error as Error).message, true); }
    finally { setBusy(false); }
  };
  const shuffle = async () => {
    if (!selected.length) return;
    setBusy(true);
    try {
      const result = await api.post<{ program: { id: string; name: string } }>("/api/library/packs/shuffle", { pack_ids: selected });
      toast(`Building “${result.program.name}” in the background. Every distinct scene will be prepared once.`);
      navigate(`/programs/${result.program.id}`);
    } catch (error) { toast((error as Error).message, true); }
    finally { setBusy(false); }
  };
  return <div className="col pack-library">
    <section className="hero pack-hero"><div className="hero-content"><div className="eyebrow">ONE FOLDER IN · HOURS OF VARIETY OUT</div><h2>Import a Halloween video package.</h2><p>Choose the package folder or ZIP. Projector keeps related scenes together, detects landscape and portrait variants, and can turn one or several packs into an endless shuffled show.</p></div></section>
    <section className="panel col">
      <div className="steps"><div className="step"><span>1</span>Name the package</div><div className="step"><span>2</span>Choose folder or ZIP</div><div className="step"><span>3</span>Shuffle it</div></div>
      <div className="row pack-import-row"><label className="grow">Package name<input value={name} onChange={event => setName(event.target.value)} placeholder="Ghostly Apparitions, Creepy Clowns…" /></label><label className={`btn primary ${busy ? "disabled" : ""}`}>{busy ? "Importing…" : "Choose folder"}<input type="file" multiple accept="video/mp4" {...({ webkitdirectory: "", directory: "" } as Record<string, string>)} disabled={busy} style={{display:"none"}} onChange={event => void importFiles(event.target.files)} /></label><label className={`btn ${busy ? "disabled" : ""}`}>{busy ? "Importing…" : "Choose ZIP"}<input type="file" accept=".zip,application/zip" disabled={busy} style={{display:"none"}} onChange={event => void importFiles(event.target.files)} /></label></div>
      <span className="small muted">Original MP4 files are stored unchanged. Projector prepares only the scenes a show needs. Re-importing the same package skips matching files.</span>
    </section>
    <div className="section-title"><div><h2>Your packages</h2><span className="small muted">Select any combination. Collections rotate as coherent families instead of becoming a random visual soup.</span></div><button className="primary" disabled={busy || !selected.length} onClick={shuffle}>Shuffle {selected.length ? `${selected.length} selected` : "selected packs"}</button></div>
    <div className="pack-grid">{packs.map(pack => {
      const checked = selected.includes(pack.id);
      return <button key={pack.id} className={`pack-card ${checked ? "selected" : ""}`} onClick={() => setSelected(value => checked ? value.filter(id => id !== pack.id) : [...value, pack.id])}>
        <div className="pack-art" style={{backgroundImage: pack.thumbnail_url ? `linear-gradient(0deg,#080b12dd,#080b1210),url(${pack.thumbnail_url})` : undefined}}><span className="pack-check">{checked ? "✓" : "+"}</span></div>
        <div className="col pack-copy"><div className="row"><strong>{pack.name}</strong>{pack.errors > 0 && <span className="tag" style={{color:"var(--bad)"}}>{pack.errors} errors</span>}</div><span className="small muted">{pack.scenes} scenes · {pack.characters} characters · {pack.files} variants</span><span className="small">{pack.prepared} ready{pack.processing ? ` · ${pack.processing} preparing` : ""}</span><div>{pack.collections.slice(0,4).map(collection => <span className="tag" key={collection}>{collection}</span>)}</div></div>
      </button>;
    })}{!packs.length && <div className="panel muted">No packages yet. Import a folder or ZIP above; the first catalogue scan starts automatically.</div>}</div>
    <details className="panel"><summary>What does the automatic analysis do?</summary><p className="small muted">Filename and folder rules suggest collection, character, surface type, orientation, background style, and buffer clips. Frame analysis adds visible-action thumbnails and movement direction. Low-confidence guesses stay reviewable in Scene catalogue. Your files are never sent to a cloud service.</p></details>
  </div>;
}

function AssetLibrary() {
  const { toast } = useLive();
  const [season, setSeason] = useState("");
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [tag, setTag] = useState("");
  const params = new URLSearchParams();
  if (season) params.set("season", season);
  if (kind) params.set("kind", kind);
  if (q) params.set("q", q);
  if (tag) params.set("tag", tag);
  const { data: assets = [] } = useFetch<MediaAsset[]>(`/api/media?${params}`, ["media"], []);
  const { data: tags = {} } = useFetch<Record<string, number>>("/api/media/tags", ["media"], {});
  const [sel, setSel] = useState<string | null>(null);
  const [upSeason, setUpSeason] = useState("halloween");
  const [upTags, setUpTags] = useState("");
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const selected = assets.find((a) => a.id === sel) ?? null;

  const upload = async (files: FileList | File[]) => {
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    fd.append("season", upSeason);
    fd.append("tags", upTags);
    setUploading(true);
    try {
      const res = await api.post<MediaAsset[]>("/api/media/upload", fd);
      toast(`Uploaded ${res.length} file(s); processing in the background.`);
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="row">
        <h1 style={{ margin: 0 }}>Library</h1>
        <span className="muted">{assets.length} items</span>
      </div>

      <div
        className="panel col"
        style={{ borderStyle: "dashed", borderColor: dragOver ? "var(--accent)" : undefined }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer.files.length) upload(e.dataTransfer.files);
        }}
      >
        <div className="row">
          <b>Add videos</b>
          <span className="muted small">
            Drop files here (your projector's AtmosFX USB clips work), or copy them into <span className="mono">data/import/&lt;season&gt;/&lt;tag&gt;/</span>.
          </span>
        </div>
        <div className="row">
          <select value={upSeason} onChange={(e) => setUpSeason(e.target.value)}>
            <option value="halloween">Halloween</option>
            <option value="christmas">Christmas</option>
            <option value="">No season</option>
          </select>
          <input placeholder="tags, e.g. ghost, atmosfx" value={upTags} onChange={(e) => setUpTags(e.target.value)} />
          <label className="btn">
            {uploading ? "Uploading…" : "Choose files"}
            <input type="file" multiple accept="video/*,audio/*,image/*" style={{ display: "none" }} onChange={(e) => e.target.files && upload(e.target.files)} />
          </label>
        </div>
      </div>

      <div className="row">
        <input placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={season} onChange={(e) => setSeason(e.target.value)}>
          <option value="">All seasons</option>
          <option value="halloween">Halloween</option>
          <option value="christmas">Christmas</option>
        </select>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All kinds</option>
          <option value="video">Video</option>
          <option value="audio">Audio</option>
          <option value="image">Image</option>
        </select>
        <select value={tag} onChange={(e) => setTag(e.target.value)}>
          <option value="">All tags</option>
          {Object.entries(tags).map(([t, n]) => (
            <option key={t} value={t}>
              {t} ({n})
            </option>
          ))}
        </select>
      </div>

      <div className="split">
        <div className="grid cards">
          {assets.map((a) => (
            <AssetCard key={a.id} a={a} selected={a.id === sel} onClick={() => setSel(a.id)} />
          ))}
        </div>
        <div>{selected ? <AssetDetail a={selected} onDeleted={() => setSel(null)} /> : <div className="panel muted">Select an item to edit it.</div>}</div>
      </div>
    </div>
  );
}

export function AssetCard({ a, selected, onClick }: { a: MediaAsset; selected?: boolean; onClick?: () => void }) {
  const [hover, setHover] = useState(false);
  const img = hover && a.preview_path ? fileUrl(a.preview_path) : fileUrl(a.thumb_path);
  const score = a.suitability?.score;
  return (
    <div className="card" style={selected ? { borderColor: "var(--accent)" } : undefined} onClick={onClick} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <div className="thumb" style={{ backgroundImage: img ? `url(${img})` : undefined }}>
        {a.status !== "ready" && (
          <span className="badge" style={{ left: 6, right: "auto", color: a.status === "error" ? "var(--bad)" : "var(--warn)" }}>
            {a.status}
          </span>
        )}
        {a.duration_ms > 0 && <span className="badge">{fmtDuration(a.duration_ms)}</span>}
      </div>
      <div className="body">
        <div className="title" title={a.title}>
          {a.title}
        </div>
        <div className="row small muted" style={{ gap: 4 }}>
          {a.season && <span className="tag">{a.season}</span>}
          {a.kind !== "video" && <span className="tag">{a.kind}</span>}
          {score != null && (
            <span className="tag" title="Window suitability: black background + motion">
              fit {Math.round(score * 100)}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function AssetDetail({ a, onDeleted }: { a: MediaAsset; onDeleted: () => void }) {
  const { toast } = useLive();
  const [title, setTitle] = useState(a.title);
  const [tags, setTags] = useState(a.tags.join(", "));
  const [preset, setPreset] = useState<Preset>(a.preset ?? {});
  const [lastId, setLastId] = useState(a.id);
  if (lastId !== a.id) {
    setLastId(a.id);
    setTitle(a.title);
    setTags(a.tags.join(", "));
    setPreset(a.preset ?? {});
  }

  const patch = (body: Partial<MediaAsset>) => api.patch(`/api/media/${a.id}`, body).catch((e) => toast(e.message, true));
  const reprocess = () =>
    api
      .post(`/api/media/${a.id}/reprocess`, { preset })
      .then(() => toast("Re-processing…"))
      .catch((e) => toast(e.message, true));

  return (
    <div className="panel col">
      {a.kind === "video" && a.rendition_path && <video src={fileUrl(a.rendition_path)} controls loop muted style={{ width: "100%", background: "#000", borderRadius: 6 }} />}
      {a.kind === "audio" && a.rendition_path && <audio src={fileUrl(a.rendition_path)} controls style={{ width: "100%" }} />}
      {a.kind === "image" && <img src={fileUrl(a.rendition_path || a.thumb_path)} style={{ width: "100%" }} />}
      {a.kind === "video" && a.status === "ready" && <ProjectorPreview assetId={a.id} label="Play clip on projector" />}
      {a.error && <div className="small" style={{ color: "var(--bad)", whiteSpace: "pre-wrap" }}>{a.error}</div>}
      <label>
        Title
        <input value={title} onChange={(e) => setTitle(e.target.value)} onBlur={() => patch({ title })} />
      </label>
      <label>
        Tags
        <input
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          onBlur={() => patch({ tags: tags.split(",").map((t) => t.trim()).filter(Boolean) })}
        />
      </label>
      <label>
        Season
        <select value={a.season} onChange={(e) => patch({ season: e.target.value })}>
          <option value="">None</option>
          <option value="halloween">Halloween</option>
          <option value="christmas">Christmas</option>
        </select>
      </label>
      <div className="small muted">
        {a.width}×{a.height} · {fmtDuration(a.duration_ms)} · {(a.size / 1e6).toFixed(1)} MB · {a.source}
        {a.suitability?.black_ratio != null && ` · ${Math.round(a.suitability.black_ratio * 100)}% black`}
      </div>
      {(a.license || a.attribution) && (
        <div className="small muted">
          {a.license} {a.attribution && `· ${a.attribution}`}
        </div>
      )}
      {a.suitability?.framing_warning && <div className="panel small" style={{color:"var(--warn)"}}>Bright content reaches the frame edge. Review against your window mask or increase the safe margin.</div>}
      <h3>Processing</h3>
      <span className="small muted">Native {(a.technical?.native_fps ?? a.technical?.native?.fps)?.toFixed(2) ?? "unknown"} FPS · delivery {a.technical?.delivery?.fps?.toFixed(2) ?? "unknown"} FPS{a.technical?.interpolation && ` · ${a.technical.interpolation}`} · playback {a.technical?.playback_verified ? "verified" : "unverified"}</span>
      {a.kind === "video" && <button onClick={()=>api.post(`/api/media/${a.id}/compatibility`).then(()=>toast("Preparing a separate 30 FPS compatibility clip")).catch(e=>toast(e.message,true))}>Prepare 30 FPS compatibility clip</button>}
      <PresetEditor preset={preset} onChange={setPreset} />
      <div className="row">
        <button onClick={reprocess}>Re-process</button>
        <button
          className="danger right"
          onClick={async () => {
            if (!confirm(`Delete "${a.title}"?`)) return;
            await api.del(`/api/media/${a.id}`);
            onDeleted();
          }}
        >
          Delete
        </button>
      </div>
    </div>
  );
}

export function PresetEditor({ preset, onChange }: { preset: Preset; onChange: (p: Preset) => void }) {
  const set = (k: keyof Preset, v: unknown) => onChange({ ...preset, [k]: v });
  return (
    <div className="col">
      <div className="field-row">
        <label>
          Trim start (s)
          <input type="number" step={0.1} value={preset.trim_start ?? 0} onChange={(e) => set("trim_start", Number(e.target.value))} />
        </label>
        <label>
          Trim end (s)
          <input type="number" step={0.1} value={preset.trim_end ?? 0} onChange={(e) => set("trim_end", Number(e.target.value))} />
        </label>
        <label>
          Loop crossfade (s)
          <input type="number" step={0.1} value={preset.loop_crossfade ?? 0} onChange={(e) => set("loop_crossfade", Number(e.target.value))} />
        </label>
        <label>
          Black crush (0-40)
          <input type="number" min={0} max={40} value={preset.black_crush ?? 12} onChange={(e) => set("black_crush", Number(e.target.value))} />
        </label>
        <label>
          Max height
          <select value={preset.max_height ?? 720} onChange={(e) => set("max_height", Number(e.target.value))}>
            <option value={720}>720p (recommended)</option>
            <option value={1080}>1080p</option>
            <option value={540}>540p</option>
          </select>
        </label>
        <label>
          FPS
          <select value={preset.fps ?? 0} onChange={(e) => set("fps", Number(e.target.value))}>
            <option value={0}>Preserve source cadence</option>
            <option value={60}>60 (requires interpolated source)</option>
            <option value={24}>24</option>
            <option value={25}>25</option>
            <option value={30}>30</option>
          </select>
        </label>
      </div>
      <label>Safe dark margin (%)<input type="number" min={0} max={20} value={Math.round((preset.safe_margin ?? 0)*100)} onChange={e=>set("safe_margin",Number(e.target.value)/100)} /></label>
      <div className="small muted">Rear-projection mirroring is configured on the device. Raising FPS alone repeats frames; it does not interpolate motion.</div>
      <div className="row">
        <label className="inline">
          <input type="checkbox" checked={preset.loudnorm ?? true} onChange={(e) => set("loudnorm", e.target.checked)} /> Normalize loudness
        </label>
      </div>
    </div>
  );
}
