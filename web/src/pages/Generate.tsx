import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ComfyJob, ComfyParam, ComfyWorkflow } from "../api";
import { useFetch } from "../hooks";
import { useLive } from "../live";
import GenerationStudio from "../components/GenerationStudio";

export default function GeneratePage() {
  const [advanced, setAdvanced] = useState(false);
  return <div className="col" style={{gap:16}}><div className="row"><button className={!advanced ? "primary" : ""} onClick={()=>setAdvanced(false)}>Creature studio</button><button className={advanced ? "primary" : ""} onClick={()=>setAdvanced(true)}>Advanced workflows</button></div>{advanced ? <AdvancedGeneratePage /> : <GenerationStudio />}</div>;
}

interface ComfyStatus {
  configured: boolean;
  ok: boolean;
  url: string;
  version?: string;
  error?: string;
  devices?: { name: string; vram_total: number; vram_free: number }[];
}

interface WorkflowNode {
  id: string;
  class_type: string;
  title: string;
  inputs: Record<string, unknown>;
}

type WorkflowDetail = ComfyWorkflow & { workflow: Record<string, unknown>; nodes: WorkflowNode[] };

function ParamField({ p, value, onChange }: { p: ComfyParam; value: unknown; onChange: (v: unknown) => void }) {
  const { toast } = useLive();
  const [uploading, setUploading] = useState(false);
  if (p.type === "text")
    return (
      <label>
        {p.label}
        <textarea rows={3} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />
      </label>
    );
  if (p.type === "image")
    return (
      <label>
        {p.label}
        <input
          type="file"
          accept="image/*"
          onChange={async (e) => {
            const f = e.target.files?.[0];
            if (!f) return;
            setUploading(true);
            try {
              const fd = new FormData();
              fd.append("image", f);
              const r = await api.post<{ name: string }>("/api/comfy/upload-image", fd);
              onChange(r.name);
            } catch (err) {
              toast((err as Error).message, true);
            } finally {
              setUploading(false);
            }
          }}
        />
        <span className="small muted">{uploading ? "Uploading…" : value ? `Uploaded: ${String(value)}` : "Upload a still, ideally on black"}</span>
      </label>
    );
  if (p.type === "seed")
    return (
      <label>
        {p.label}
        <span className="row" style={{ gap: 4 }}>
          <input type="number" value={Number(value ?? -1)} onChange={(e) => onChange(Number(e.target.value))} style={{ width: 160 }} />
          <button className="small" onClick={() => onChange(-1)} title="-1 picks a random seed each run">
            random
          </button>
        </span>
      </label>
    );
  return (
    <label>
      {p.label}
      <input
        type={p.type === "int" || p.type === "float" ? "number" : "text"}
        step={p.type === "float" ? 0.1 : 1}
        value={String(value ?? "")}
        onChange={(e) => onChange(p.type === "int" || p.type === "float" ? Number(e.target.value) : e.target.value)}
      />
    </label>
  );
}

function MappingEditor({ wf, onSaved }: { wf: WorkflowDetail; onSaved: () => void }) {
  const { toast } = useLive();
  const [params, setParams] = useState<ComfyParam[]>(wf.params);
  useEffect(() => setParams(wf.params), [wf]);
  const set = (i: number, patch: Partial<ComfyParam>) => setParams(params.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  return (
    <div className="col">
      <div className="small muted">Map form fields to node inputs. Only literal inputs (not links) are listed.</div>
      {params.map((p, i) => {
        const node = wf.nodes.find((n) => n.id === p.node);
        return (
          <div key={i} className="row small">
            <input value={p.label} onChange={(e) => set(i, { label: e.target.value, name: e.target.value.toLowerCase().replace(/\W+/g, "_") })} style={{ width: 130 }} />
            <select value={p.type} onChange={(e) => set(i, { type: e.target.value as ComfyParam["type"] })}>
              {["text", "string", "int", "float", "seed", "image"].map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
            <select value={p.node} onChange={(e) => set(i, { node: e.target.value, input: Object.keys(wf.nodes.find((n) => n.id === e.target.value)?.inputs ?? {})[0] ?? "" })}>
              {wf.nodes.map((n) => (
                <option key={n.id} value={n.id}>
                  #{n.id} {n.title || n.class_type}
                </option>
              ))}
            </select>
            <select value={p.input} onChange={(e) => set(i, { input: e.target.value, default: node?.inputs[e.target.value] })}>
              {Object.keys(node?.inputs ?? {}).map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
            <button className="small" onClick={() => setParams(params.filter((_, j) => j !== i))}>
              ✕
            </button>
          </div>
        );
      })}
      <div className="row">
        <button
          className="small"
          onClick={() => {
            const n = wf.nodes[0];
            const input = Object.keys(n?.inputs ?? {})[0] ?? "";
            setParams([...params, { name: `field${params.length + 1}`, label: `Field ${params.length + 1}`, type: "string", default: n?.inputs[input] ?? "", node: n?.id ?? "", input }]);
          }}
        >
          + Field
        </button>
        <button
          className="primary right"
          onClick={async () => {
            try {
              await api.put(`/api/comfy/workflows/${wf.id}`, { params });
              toast("Mapping saved");
              onSaved();
            } catch (e) {
              toast((e as Error).message, true);
            }
          }}
        >
          Save mapping
        </button>
      </div>
    </div>
  );
}

export function AdvancedGeneratePage() {
  const { toast } = useLive();
  const { data: status } = useFetch<ComfyStatus>("/api/comfy/status", ["settings"]);
  const { data: workflows = [], reload: reloadWorkflows } = useFetch<ComfyWorkflow[]>("/api/comfy/workflows", ["comfy_workflows"], []);
  const { data: jobs = [] } = useFetch<ComfyJob[]>("/api/comfy/jobs", ["comfy_jobs"], []);
  const [wid, setWid] = useState("");
  const [detail, setDetail] = useState<WorkflowDetail | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [season, setSeason] = useState("halloween");
  const [tags, setTags] = useState("");
  const [count, setCount] = useState(1);
  const [showMapping, setShowMapping] = useState(false);

  const current = wid || workflows[0]?.id || "";
  const loadDetail = () => {
    if (!current) return;
    api.get<WorkflowDetail>(`/api/comfy/workflows/${current}`).then((d) => {
      setDetail(d);
      setValues(Object.fromEntries(d.params.map((p) => [p.name, p.default])));
    });
  };
  useEffect(loadDetail, [current]); // eslint-disable-line react-hooks/exhaustive-deps

  const generate = async () => {
    try {
      await api.post("/api/comfy/jobs", {
        workflow_id: current,
        params: values,
        season,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        count,
      });
      toast(count > 1 ? `Queued ${count} variations` : "Queued");
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const uploadWorkflow = async (f: File) => {
    try {
      const wf = JSON.parse(await f.text());
      const created = await api.post<WorkflowDetail>("/api/comfy/workflows", { name: f.name.replace(/\.json$/i, ""), workflow: wf });
      reloadWorkflows();
      setWid(created.id);
      setShowMapping(true);
      toast("Workflow added. Check the field mapping.");
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const wfName = (id: string) => workflows.find((w) => w.id === id)?.name ?? "workflow";

  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="row">
        <h1 style={{ margin: 0 }}>Generate</h1>
        <span className="muted small">Make custom clips and sounds with your ComfyUI server. Results land in the library automatically.</span>
      </div>

      <div className="panel row small">
        {!status ? (
          <span className="muted">Checking ComfyUI…</span>
        ) : !status.configured ? (
          <span>
            ComfyUI isn't set up. <Link to="/settings">Enter its URL in Settings</Link> (for example http://192.168.1.20:8188).
          </span>
        ) : status.ok ? (
          <span>
            <span className="dot" style={{ background: "var(--ok)" }} /> Connected to {status.url}
            {status.version && ` · v${status.version}`}
            {status.devices?.map((d) => ` · ${d.name.split(":").pop()?.trim()} ${Math.round(d.vram_free / 1024 ** 3)}/${Math.round(d.vram_total / 1024 ** 3)} GB free`)}
          </span>
        ) : (
          <span style={{ color: "var(--bad)" }}>
            Can't reach {status.url}: {status.error}
          </span>
        )}
      </div>

      <div className="split">
        <div className="panel col">
          <div className="row">
            <select value={current} onChange={(e) => (setWid(e.target.value), setShowMapping(false))}>
              {workflows.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.kind === "audio" ? "♪ " : "▶ "}
                  {w.name}
                  {w.builtin ? "" : " (custom)"}
                </option>
              ))}
            </select>
            <label className="small inline" title="Upload a workflow saved with Export (API)">
              <input type="file" accept=".json,application/json" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && uploadWorkflow(e.target.files[0])} />
              <span className="tag" style={{ cursor: "pointer" }}>
                + Upload workflow
              </span>
            </label>
            {detail && !detail.builtin && (
              <>
                <button className="small" onClick={() => setShowMapping(!showMapping)}>
                  {showMapping ? "Hide mapping" : "Edit mapping"}
                </button>
                <button
                  className="small danger"
                  onClick={async () => {
                    if (!confirm(`Delete workflow "${detail.name}"?`)) return;
                    await api.del(`/api/comfy/workflows/${detail.id}`);
                    setWid("");
                    reloadWorkflows();
                  }}
                >
                  Delete
                </button>
              </>
            )}
          </div>
          {detail?.description && <div className="small muted">{detail.description}</div>}
          {detail && showMapping && <MappingEditor wf={detail} onSaved={loadDetail} />}
          {detail?.params.map((p) => (
            <ParamField key={p.name} p={p} value={values[p.name]} onChange={(v) => setValues({ ...values, [p.name]: v })} />
          ))}
          <div className="field-row">
            <label>
              Season
              <select value={season} onChange={(e) => setSeason(e.target.value)}>
                <option value="halloween">Halloween</option>
                <option value="christmas">Christmas</option>
                <option value="">None</option>
              </select>
            </label>
            <label>
              Tags
              <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="ghost, left-to-right" />
            </label>
            <label>
              Variations
              <input type="number" min={1} max={16} value={count} onChange={(e) => setCount(Number(e.target.value))} />
            </label>
          </div>
          <button className="primary" disabled={!status?.ok || !current} onClick={generate}>
            Generate
          </button>
        </div>

        <div className="panel col" style={{ alignSelf: "flex-start" }}>
          <b>Jobs</b>
          {jobs.length === 0 && <span className="small muted">No jobs yet.</span>}
          {jobs.map((j) => (
            <div key={j.id} className="col" style={{ gap: 4, borderBottom: "1px solid var(--border)", paddingBottom: 8 }}>
              <div className="row small">
                <b>{wfName(j.workflow_id)}</b>
                <span className="tag">{j.status}</span>
                {(j.status === "queued" || j.status === "running") && (
                  <button className="small right" onClick={() => api.del(`/api/comfy/jobs/${j.id}`)}>
                    Cancel
                  </button>
                )}
              </div>
              <span className="small muted" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {String(j.params.prompt ?? j.params.tags ?? "")}
              </span>
              {j.status === "running" && (
                <div className="progress">
                  <div style={{ width: `${j.progress * 100}%` }} />
                </div>
              )}
              {j.status === "error" && <span className="small" style={{ color: "var(--bad)" }}>{j.error}</span>}
              {j.status === "done" && (
                <Link to="/library" className="small">
                  {j.asset_ids.length} clip(s) in library
                </Link>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
