import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, Cue, fmtDuration, MediaAsset, Program, Surface } from "../api";
import { useLive } from "../live";
import { useFetch } from "../hooks";
import HousePreview from "../components/HousePreview";
import Icon from "../components/Icon";

export default function Dashboard() {
  const { devices, runner, toast, connected } = useLive();
  const { data: programs = [] } = useFetch<Program[]>("/api/programs", ["programs"], []);
  const { data: surfaces = [] } = useFetch<Surface[]>("/api/surfaces", ["layout"], []);
  const { data: assets = [] } = useFetch<MediaAsset[]>("/api/media?kind=video", ["media"], []);
  const [selected, setSelected] = useState("");
  const [oneshotProgram, setOneshotProgram] = useState("");
  const players = devices.filter((d) => d.role === "player");
  const surfaceName = (id: string | null) => surfaces.find((s) => s.id === id)?.name ?? "unassigned";

  const call = async (path: string, body?: unknown) => {
    try {
      await api.post(path, body);
    } catch (e) {
      toast(String((e as Error).message), true);
    }
  };

  const programId = selected || runner.program_id || programs[0]?.id || "";
  const shotSource = programs.find((p) => p.id === (oneshotProgram || runner.program_id || programs[0]?.id));
  const shotCues = useMemo(() => {
    const out: Cue[] = [];
    for (const it of shotSource?.items ?? []) {
      if (it.type === "block") out.push(...it.items);
      else if (it.type !== "scheduler") out.push(it);
    }
    return out;
  }, [shotSource]);

  const now = runner.now ?? Date.now();
  const cur = runner.current;
  const curProgress = cur ? Math.min(1, Math.max(0, (now - cur.start) / Math.max(1, cur.end - cur.start))) : 0;
  const sheet = runner.run_sheet;
  const activeIds = new Set(sheet?.active_characters.map(c => c.id) ?? []);

  return (
    <div className="col" style={{ gap: 20 }}>
      <div className="page-heading">
        <div><span className="eyebrow">YOUR CONTROL ROOM</span><h1>Set the scene.</h1><p>Every window, one show.</p></div>
        <Link className="btn icon-button" to="/devices" aria-label="Manage players"><Icon name="devices" /></Link>
      </div>
      <section className="hero">
        <svg className="hero-decoration" viewBox="0 0 200 180" fill="none" aria-hidden="true"><path d="M12 60h48v108H12zM75 20h50v148H75zM140 45h48v123h-48z" stroke="#ffa158" strokeWidth="1.5"/><path d="M36 60v108M12 110h48M100 20v148M75 94h50M164 45v123M140 106h48" stroke="#ffa158" opacity=".35"/><path d="M25 20h20M35 10v20m110-6h12m-6-6v12" stroke="#79e4bc"/></svg>
        <div className="hero-content"><span className="eyebrow">{runner.state === "playing" ? "ON THE WINDOWS" : "READY WHEN YOU ARE"}</span><h2>{runner.state === "playing" ? runner.program_name : "A little darkness.\nA lot of possibility."}</h2><p>{runner.state === "playing" ? "Your players are running their cached show together." : "Build a moment, fill your windows, and bring the night to life."}</p>
        <div className="stat-strip"><div><strong>{players.filter(d => d.online).length}<span> / {players.length}</span></strong><span>players online</span></div><div><strong>{programs.length}</strong><span>saved shows</span></div><div><strong>{connected ? "LAN" : "Offline"}</strong><span>{connected ? "server connected" : "reconnecting"}</span></div></div></div>
      </section>
      <div className="section-title"><h2>Show controls</h2><Link to="/programs">All programs →</Link></div>
      <div className="panel col transport">
        <div className="row">
          <select value={programId} onChange={(e) => setSelected(e.target.value)}>
            {programs.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <button className="primary" disabled={!programId} onClick={() => call("/api/runner/start", { program_id: programId })}>
            <Icon name="live" size={17} /> Play show
          </button>
          {runner.state === "playing" && <button onClick={() => call("/api/runner/pause")}>Pause</button>}
          {runner.state === "paused" && <button onClick={() => call("/api/runner/resume")}>Resume</button>}
          {runner.state === "playing" && <button onClick={() => call("/api/runner/skip")}>Skip cue</button>}
          {runner.state === "syncing" && <button onClick={() => call("/api/runner/force")}>Start without waiting</button>}
          {runner.state !== "idle" && (
            <button className="danger" onClick={() => call("/api/runner/stop")}>
              Stop
            </button>
          )}
          <span className="right transport-state">
            {runner.state.toUpperCase()}
            {runner.program_name ? ` · ${runner.program_name}` : ""}
          </span>
        </div>
        {runner.message && <div className="small" style={{ color: "var(--warn)" }}>{runner.message}</div>}
        {runner.state === "syncing" && (
          <div className="col small">
            {Object.entries(runner.sync_progress ?? {}).map(([dev, p]) => (
              <div key={dev} className="row">
                <span style={{ width: 140 }}>{devices.find((d) => d.id === dev)?.name ?? dev}</span>
                <div className="progress grow">
                  <div style={{ width: `${Math.round(p * 100)}%` }} />
                </div>
                <span>{Math.round(p * 100)}%</span>
              </div>
            ))}
          </div>
        )}
        {cur && (
          <div className="col small">
            <div className="row">
              <b>Now:</b> {cur.name}
              <span className="muted">
                {fmtDuration(Math.max(0, now - cur.start))} / {fmtDuration(cur.end - cur.start)}
              </span>
              {runner.next && (
                <span className="right">
                  <b>Next:</b> {runner.next.name} in {fmtDuration(Math.max(0, runner.next.start - now))}
                </span>
              )}
            </div>
            <div className="progress">
              <div style={{ width: `${curProgress * 100}%` }} />
            </div>
          </div>
        )}
      </div>

      {runner.state === "playing" && sheet && (
        <section className="panel col" style={{ gap: 14 }}>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <div className="grow"><div className="eyebrow">LIVE RUN SHEET</div><h2 style={{ margin: 0 }}>{sheet.kind === "marathon" ? "Character scheduler" : sheet.kind === "shuffle" ? "Shuffled show queue" : "Program queue"}</h2></div>
            {sheet.collection_group && <span className="tag" style={{ borderColor: "var(--accent)", color: "var(--accent)" }}>Collection now · {sheet.collection_group.name}</span>}
            {sheet.source_programs.map(name => <span className="tag" key={name}>{name}</span>)}
          </div>

          {!!sheet.active_characters.length && <div className="col">
            <b>Selected now</b>
            <div className="grid cards" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))" }}>
              {sheet.active_characters.map(c => <div className="card body" key={c.id}>
                <strong>{c.name}</strong>{c.source_program && <span className="small muted">{c.source_program}</span>}
                <span className="small">Appearance {Math.min(c.appearances_done + 1, c.appearances_total)} of {c.appearances_total}</span>
                <div className="progress"><div style={{ width: `${Math.min(100, c.appearances_done / Math.max(1,c.appearances_total) * 100)}%` }} /></div>
              </div>)}
            </div>
            {sheet.next_rotation && <span className="small muted">Next character draw in {fmtDuration(Math.max(0, sheet.next_rotation - now))}{sheet.character_bag_remaining.length ? ` · still in this shuffle bag: ${sheet.character_bag_remaining.join(", ")}` : " · bag refills after this rotation"}</span>}
          </div>}

          <div className="col">
            <b>Roster <span className="muted small">{sheet.roster.length} entries</span></b>
            <div className="row" style={{ flexWrap: "wrap" }}>{sheet.roster.map(c => <span key={c.id} className="tag" style={activeIds.has(c.id) ? { borderColor: "var(--accent)", color: "var(--accent)" } : undefined} title={`${c.clip_count} clip${c.clip_count === 1 ? "" : "s"}${c.source_program ? ` · ${c.source_program}` : ""}`}>{c.name} · {c.clip_count}</span>)}</div>
          </div>

          <div className="col">
            <b>Scheduled movements and appearances</b>
            {!sheet.movements.length && <span className="muted small">No more scheduled appearances in the current horizon.</span>}
            {sheet.movements.slice(0, 16).map((m, i) => <div className="row small" key={`${m.start}-${m.surface_id}-${m.asset_id}-${i}`} style={{ padding: "7px 0", borderBottom: "1px solid var(--border)" }}>
              <span className={`status-pill ${m.state === "playing" ? "online" : ""}`}>{m.state === "playing" ? "NOW" : `in ${fmtDuration(Math.max(0,m.start-now))}`}</span>
              <b style={{ minWidth: 135 }}>{m.character_name}</b>
              <span>→ {m.surface_name}</span>
              {m.motion_reason && <span className="tag" title={m.motion_reason}>{m.flip_h ? "auto-flipped" : "native direction"}</span>}
              <span className="muted grow" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.clip_name}</span>
              <span className="muted">{fmtDuration(m.end-m.start)}</span>
            </div>)}
          </div>

          {!!sheet.upcoming_selections.length && <details><summary>Upcoming scheduler decisions</summary><div className="col small">{sheet.upcoming_selections.map((u,i)=><div className="row" key={`${u.start}-${i}`}><b>{u.name}</b><span className="muted">{u.start <= now ? "current rotation" : `in ${fmtDuration(u.start-now)}`}</span></div>)}</div></details>}
        </section>
      )}

      <div className="quick-links">
        <Link to="/calibrate" className="quick-link"><Icon name="calibrate" size={25} /><div><strong>Fit your windows</strong><small>Camera-guided calibration</small></div><Icon name="arrow" size={16} /></Link>
        <Link to="/library" className="quick-link"><Icon name="library" size={25} /><div><strong>Find the right scene</strong><small>Your projection library</small></div></Link>
        <Link to="/setup" className="quick-link"><Icon name="plus" size={25} /><div><strong>Set up a player</strong><small>Connect a new window</small></div></Link>
      </div>

      {runner.state === "playing" && runner.program_id && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>House view</h3>
          <HousePreview programId={runner.program_id} surfaces={surfaces} assets={assets} liveStart={runner.started_at} />
        </div>
      )}

      <details className="panel details-panel">
        <summary>Quick cues <span className="small muted">Live effects</span></summary>
        <div className="row">
          <h3 style={{ margin: 0 }}>One-shots</h3>
          <select value={shotSource?.id ?? ""} onChange={(e) => setOneshotProgram(e.target.value)}>
            {programs.map((p) => (
              <option key={p.id} value={p.id}>
                cues from {p.name}
              </option>
            ))}
          </select>
          <span className="muted small">Fire any cue on top of the running program; it resumes afterwards.</span>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          {shotCues.length === 0 && <span className="muted">No cues.</span>}
          {shotCues.map((c) => (
            <span key={c.id} className="row" style={{ gap: 2 }}>
              <button disabled={runner.state !== "playing"} onClick={() => call("/api/runner/oneshot", { cue: c, immediate: true })}>
                {c.name || c.pattern}
              </button>
              <button
                className="small"
                title="At next cue boundary"
                disabled={runner.state !== "playing"}
                onClick={() => call("/api/runner/oneshot", { cue: c, immediate: false })}
              >
                ⏭
              </button>
            </span>
          ))}
        </div>
      </details>

      <section className="col">
        <div className="section-title"><h2>Your players</h2><Link to="/devices">Manage →</Link></div>
        {players.length === 0 ? (
          <p className="muted">
            No players yet. Install the app on each streaming stick and enter this server's address (see{" "}
            <Link to="/settings">Settings</Link>).
          </p>
        ) : (
          <div className="player-grid">
              {players.map((d) => {
                const st = d.status ?? {};
                const drift = st.drift_ms ?? 0;
                return (
                  <Link key={d.id} className="player-card" to={`/devices/${d.id}`}>
                    <div className="row"><span className="device-symbol"><Icon name="devices" size={24} /></span><span className={`status-pill right ${d.online ? "online" : ""}`}><span className={`dot ${d.online ? "on" : ""}`} />{!d.online ? "Offline" : st.management?.playback_state === "playing" ? "Playing" : "Standby"}</span></div>
                    <h2>{d.name}</h2><p>{surfaceName(d.surface_id)}</p>
                    <div className="player-metrics"><span>{st.cache_ready ? "Media ready" : d.online ? "Connected" : "Waiting for player"}</span><span>{d.online ? `${drift} ms drift` : "—"}</span><span>{st.storage_free_mb != null ? `${(st.storage_free_mb / 1024).toFixed(1)} GB free` : ""}</span></div>
                  </Link>
                );
              })}
          </div>
        )}
      </section>
    </div>
  );
}
