import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Surface } from "../api";
import { useFetch } from "../hooks";
import { useLive } from "../live";
import Icon from "../components/Icon";

type Job = { id: string; name: string; ip: string; state: string; step: string; message: string; completed: string[]; device_id?: string };
const steps = ["Configure", "Maintenance", "Takeover", "Boot", "Debloat"];
const labels = ["Install & power", "Recovery link", "Projector Home", "Restart check", "Clean up apps"];

export default function SetupPage() {
  const { id } = useParams();
  return id ? <PlayerSetup id={id} /> : <NewStick />;
}

function PlayerSetup({ id }: { id: string }) {
  const { devices, toast } = useLive();
  const device = devices.find(d => d.id === id && d.role === "player");
  const { data: surfaces = [] } = useFetch<Surface[]>("/api/surfaces", ["layout"], []);
  const [name, setName] = useState("");
  const [surface, setSurface] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (device) { setName(device.name); setSurface(device.surface_id ?? ""); } }, [device?.id]);
  async function save() {
    setBusy(true);
    try { await api.patch(`/api/devices/${id}`, { name: name.trim(), surface_id: surface || null }); toast("Player assignment saved"); }
    catch (e) { toast(String(e), true); } finally { setBusy(false); }
  }
  return <div className="col setup-page">
    <div className="page-heading"><div><div className="eyebrow">CONNECTED BY QR</div><h1>Give it a place.</h1><p className="lead">One player. One window into your show.</p></div><span className="heading-icon"><Icon name="devices" size={30} /></span></div>
    {!device ? <div className="panel">Waiting for this player to register. Check that it is connected to this server. <Link to="/devices">View players</Link></div> : <>
      <div className="panel col"><span className={`connection-pill ${device.online ? "online" : ""}`}>{device.online ? "Player connected" : "Player offline"}</span><label>Player name<input value={name} maxLength={80} onChange={e => setName(e.target.value)} /></label><label>Assigned surface<select value={surface} onChange={e => setSurface(e.target.value)}><option value="">Choose a surface</option>{surfaces.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label><p className="muted">Programs target this surface. Moving the stick to a projector keeps its assignment and cached clips.</p><button className="primary" disabled={busy || !name.trim()} onClick={save}>Save player assignment</button><Link to="/layout">Create or arrange surfaces →</Link></div>
      <div className="panel col"><h2>When your projector is in position</h2><p>Connect HDMI, then use the phone camera to align the image to the window. Set rear-projection mirroring once in player settings.</p><div className="row"><Link className="btn primary" to="/calibrate">Open calibration</Link><Link className="btn" to={`/devices/${id}`}>Player settings</Link></div></div>
    </>}
  </div>;
}

function NewStick() {
  const [stage, setStage] = useState(0);
  const [name, setName] = useState("");
  const [ip, setIp] = useState("");
  const [pairPort, setPairPort] = useState("");
  const [code, setCode] = useState("");
  const [port, setPort] = useState("");
  const [dedicated, setDedicated] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<string>(() => localStorage.getItem("projector.setup.job") ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const job = jobs.find(j => j.id === selected);
  useEffect(() => {
    let active = true;
    const poll = () => api.get<Job[]>("/api/setup/jobs").then(data => { if (active) setJobs(data); }).catch(e => { if (active) setError(e.message); });
    poll(); const timer = window.setInterval(poll, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  function select(j: Job) { setSelected(j.id); localStorage.setItem("projector.setup.job", j.id); setCode(""); setPairPort(""); setPort(""); setError(""); }
  async function submit(resume: boolean) {
    setBusy(true); setError("");
    try {
      const credentials = { pairing_port: pairPort ? Number(pairPort) : null, pairing_code: code, connection_port: port ? Number(port) : null };
      const result = await api.post<Job>(resume ? `/api/setup/jobs/${job!.id}/resume` : "/api/setup/jobs", resume ? credentials : { ...credentials, name, ip, dedicated });
      setJobs(prev => [result, ...prev.filter(j => j.id !== result.id)]); select(result);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const credentials = <><div className="setup-fields"><label>Pairing port<input inputMode="numeric" placeholder="e.g. 37907" value={pairPort} onChange={e => setPairPort(e.target.value.replace(/\D/g, "").slice(0, 5))} /></label><label>Six-digit pairing code<input inputMode="numeric" autoComplete="off" placeholder="123456" value={code} onChange={e => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} /></label></div><details><summary>Connection port / already paired</summary><label>IP address &amp; port screen → connection port<input inputMode="numeric" placeholder="Optional, different from pairing port" value={port} onChange={e => setPort(e.target.value.replace(/\D/g, "").slice(0, 5))} /></label><p className="small muted">The server tries discovery first. Pairing to this server is separate from pairing to a developer laptop.</p></details></>;
  return <div className="col setup-page">
    <div className="page-heading"><div><div className="eyebrow">ADD A PROJECTION PLAYER</div><h1>From stick to stage.</h1><p className="lead">A little setup once. Then control it from here.</p></div><span className="heading-icon"><Icon name="qr" size={30} /></span></div>
    <div className="panel setup-note"><Icon name="phone" /><div>Already running Projector?<p>Open the stick’s maintenance screen by holding Back. On this phone, use <strong>••• → Scan a player QR</strong> to name it, assign its surface and calibrate.</p></div></div>
    {error && <div className="panel error" role="alert">{error}</div>}
    {job ? <div className="panel col">
      <div className="eyebrow">{job.name} · {job.ip}</div><h2>{job.state === "complete" ? "Ready for its first show." : "Setting the stage."}</h2><p role="status" aria-live="polite">{job.message}</p>
      <ol className="setup-progress">{steps.map((s, i) => <li key={s} className={job.completed.includes(s) ? "done" : job.step === s.toLowerCase() ? "current" : ""}><span>{job.completed.includes(s) ? <Icon name="check" size={16} /> : i + 1}</span>{labels[i]}</li>)}</ol>
      {job.state === "complete" ? <Link className="btn primary" to={job.device_id ? `/devices/${job.device_id}/setup` : "/devices"}>Choose a surface</Link> : !["running", "queued"].includes(job.state) && <><p className="small muted">Keep Wireless debugging enabled. If its port changed, enter the new connection port below. Only enter a fresh pairing code if pairing is needed again.</p>{credentials}<button className="primary" disabled={busy || (!!code !== !!pairPort) || (!!code && code.length !== 6)} onClick={() => submit(true)}>Resume saved setup</button></>}
      {!["running", "queued"].includes(job.state) && <button onClick={() => { setSelected(""); localStorage.removeItem("projector.setup.job"); setStage(0); }}>Set up another stick</button>}
      <p className="small muted">Original settings and restore commands are saved on the server under ProjectorAppData → devices → {job.name}. Nothing is uninstalled.</p>
    </div> : <>
      <div className="setup-step-nav">{["Connect", "Enable pairing", "Make it yours"].map((s, i) => <button key={s} className={stage === i ? "active" : ""} onClick={() => setStage(i)}><span>0{i + 1}</span>{s}</button>)}</div>
      <div className="panel col setup-tutorial">
        {stage === 0 && <><div className="eyebrow">01 / ON YOUR TV</div><h2>Start with the basics.</h2><ol><li>Plug the stick into HDMI and its power supply. Finish the stock Android / Google TV setup.</li><li>Connect the stick and your phone to the same home LAN. Avoid guest Wi-Fi.</li><li>In Android Settings → Network, reconnect your Wi-Fi there if the stock setup wizard created the connection. We check this before replacing Home.</li></ol><p className="muted">Reserve the stick’s IP in your router if possible. The phone stays a controller; these changes apply only to the TV stick you select.</p><button className="primary" onClick={() => setStage(1)}>Connected. What’s next?</button></>}
        {stage === 1 && <><div className="eyebrow">02 / ONE-TIME ANDROID PERMISSION</div><h2>Let your server in.</h2><ol><li>On the TV: Settings → System → About. Select <strong>Android TV OS build</strong> seven times to enable developer options.</li><li>Go back to System → Developer options. Enable debugging, then <strong>Wireless debugging</strong>. Allow it on your home network; choose “Always allow” if offered.</li><li>Open <strong>Pair device with pairing code</strong>. Leave that screen open. The next step needs its IP, pairing port and six-digit code.</li></ol><p className="muted">Menu names vary by firmware. If Wireless debugging is missing, this automatic flow cannot bootstrap that model; use the documented ADB installation route. QR setup becomes available once Projector is installed.</p><button className="primary" onClick={() => setStage(2)}>I can see the pairing code</button></>}
        {stage === 2 && <form className="col" onSubmit={e => { e.preventDefault(); submit(false); }}><div className="eyebrow">03 / BACK ON YOUR PHONE</div><h2>Meet your next player.</h2><label>Player name<input required pattern="[A-Za-z][A-Za-z0-9_-]{0,31}" placeholder="p3 or front-window" value={name} onChange={e => setName(e.target.value)} /></label><label>Stick’s IP address<input required inputMode="decimal" placeholder="192.168.1.68" value={ip} onChange={e => setIp(e.target.value.trim())} /></label>{credentials}<label className="setup-consent"><input type="checkbox" checked={dedicated} onChange={e => setDedicated(e.target.checked)} /><span>Make this a dedicated projection stick: install Projector, set it as Home, keep it awake, disable CEC power control, enable maintenance recovery, and reversibly disable the listed entertainment apps.</span></label><details><summary>Which apps and settings?</summary><p>Tubi, YouTube TV, Disney+, Instagram Airwave, Haystack, Hulu, Prime Video, Play Games, Netflix and its token manager, YouTube, YouTube Music, Google TV launcher and ambient content—only where installed. The competing setup helper is disabled only after the Wi-Fi ownership check. Android Settings, network, remote, media and update services stay available.</p><p>A snapshot and restore script are saved before changes. Setup includes a reboot check. Your phone receives no player-control permissions.</p></details><button className="primary" type="submit" disabled={busy || !dedicated || (!!code !== !!pairPort) || (!!code && code.length !== 6)}>Pair &amp; set up this stick</button></form>}
      </div>
      {jobs.length > 0 && <div className="panel col"><h3>Previous setups</h3>{jobs.map(j => <button key={j.id} className="row" onClick={() => select(j)}>{j.name}<span className="muted">{j.ip}</span><span className="right">{j.state.replaceAll("_", " ")}</span></button>)}</div>}
    </>}
  </div>;
}
