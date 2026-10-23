import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Preset } from "../api";
import { useLive } from "../live";
import { useFetch } from "../hooks";
import { PresetEditor } from "./Library";

interface Info {
  server_url: string;
  apk_available: boolean;
  apk_url: string;
  players_online: number;
}

export default function SettingsPage() {
  const { toast } = useLive();
  const { data: info } = useFetch<Info>("/api/info");
  const { data: settings } = useFetch<Record<string, string>>("/api/settings");
  const { data: presetData } = useFetch<Preset>("/api/media/default-preset");
  const [form, setForm] = useState<Record<string, string>>({});
  const [preset, setPreset] = useState<Preset>({});
  useEffect(() => settings && setForm(settings), [settings]);
  useEffect(() => presetData && setPreset(presetData), [presetData]);

  const save = async () => {
    try {
      setForm(await api.put("/api/settings", form));
      toast("Settings saved");
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  return (
    <div className="col" style={{ gap: 16, maxWidth: 900 }}>
      <h1>Settings</h1>
      {info && (
        <div className="panel">
          <div className="row" style={{ alignItems: "flex-start", gap: 24 }}>
            <div className="col" style={{ alignItems: "center" }}>
              <img src={`/api/qr?data=${encodeURIComponent(info.server_url)}`} width={160} height={160} style={{ borderRadius: 6 }} />
              <b>Server</b>
              <span className="mono small">{info.server_url}</span>
            </div>
            <div className="col" style={{ alignItems: "center" }}>
              {info.apk_available ? (
                <>
                  <img src={`/api/qr?data=${encodeURIComponent(info.apk_url)}`} width={160} height={160} style={{ borderRadius: 6 }} />
                  <a href="/download/app.apk">
                    <b>Download app (APK)</b>
                  </a>
                </>
              ) : (
                <div className="muted small" style={{ width: 200 }}>
                  APK not built yet. Run <span className="mono">docker compose -f docker/docker-compose.yml --profile apk run --rm apk</span>.
                </div>
              )}
            </div>
            <div className="col small grow">
              <b>Pairing</b>
              <span>
                Players: open the app on the stick and enter <span className="mono">{info.server_url.replace("http://", "")}</span> with the remote (or pick the auto-discovered server).
              </span>
              <span>Phone: open the app and scan the server QR code.</span>
              <span className="muted">
                If this address isn't your PC's LAN IP (common in Docker), set <span className="mono">PROJECTOR_PUBLIC_HOST</span> and restart.
              </span>
            </div>
          </div>
        </div>
      )}

      <div className="panel col">
        <h3 style={{ marginTop: 0 }}>Catalog sources</h3>
        <span className="small muted">Internet Archive and Wikimedia need no key. Free keys unlock more sources.</span>
        <label>
          Pexels API key (<a href="https://www.pexels.com/api/" target="_blank">get one</a>)
          <input value={form.pexels_api_key ?? ""} onChange={(e) => setForm({ ...form, pexels_api_key: e.target.value })} />
        </label>
        <label>
          Pixabay API key (<a href="https://pixabay.com/api/docs/" target="_blank">get one</a>)
          <input value={form.pixabay_api_key ?? ""} onChange={(e) => setForm({ ...form, pixabay_api_key: e.target.value })} />
        </label>
        <label>
          Freesound API key (<a href="https://freesound.org/apiv2/apply/" target="_blank">get one</a>)
          <input value={form.freesound_api_key ?? ""} onChange={(e) => setForm({ ...form, freesound_api_key: e.target.value })} />
        </label>
        <h3>ComfyUI</h3>
        <label>Local storyboard planner URL<input placeholder="http://127.0.0.1:8081/v1" value={form.planner_url ?? ""} onChange={e=>setForm({...form,planner_url:e.target.value})} /></label>
        <label>Planner model name<input placeholder="local" value={form.planner_model ?? ""} onChange={e=>setForm({...form,planner_model:e.target.value})} /></label>
        <label>
          ComfyUI URL (e.g. http://192.168.1.50:8188)
          <input value={form.comfy_url ?? ""} onChange={(e) => setForm({ ...form, comfy_url: e.target.value })} />
        </label>
        <button className="primary" onClick={save} style={{ alignSelf: "flex-start" }}>
          Save
        </button>
      </div>

      <div className="panel col">
        <h3 style={{ marginTop: 0 }}>Default processing preset</h3>
        <span className="small muted">Applied to new imports. 720p H.264 is what the streaming sticks decode best.</span>
        <PresetEditor preset={preset} onChange={setPreset} />
        <button
          style={{ alignSelf: "flex-start" }}
          onClick={() =>
            api
              .put("/api/media/default-preset", preset)
              .then(() => toast("Default preset saved"))
              .catch((e) => toast(e.message, true))
          }
        >
          Save preset
        </button>
      </div>

      <div className="panel">
        <Link to="/credits">Credits and licenses for downloaded media</Link>
      </div>
    </div>
  );
}
