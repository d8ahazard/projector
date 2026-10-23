export type Pt = [number, number];

export interface Surface {
  id: string;
  name: string;
  x: number;
  y: number;
  width: number;
  height: number;
  groups: string[];
  sort: number;
}

export interface Device {
  id: string;
  name: string;
  role: string;
  surface_id: string | null;
  output_width: number;
  output_height: number;
  mirror_h: boolean;
  mirror_v: boolean;
  corners: Pt[];
  mask: Pt[];
  feather: number;
  brightness: number;
  gamma: number;
  gain_r: number;
  gain_g: number;
  gain_b: number;
  volume: number;
  audio: boolean;
  cache_budget_mb: number;
  capabilities: Record<string, unknown>;
  status: DeviceStatus;
  last_seen: number;
  online?: boolean;
}

export interface DeviceStatus {
  command?: { id: string; action: string; state: string; message?: string };
  management?: { state?: string; message?: string; version_code?: number; install_permission?: boolean; boot_permission?: boolean; appliance_mode?: boolean; default_home?: boolean; playback_state?: string; loaded_decoders?: number; clip_loads?: number; buffer_events?: number; buffer_ms?: number; dropped_frames?: number; playback_errors?: number; continuous_rendering?: boolean; maintenance_adb?: boolean; wireless_adb_enabled?: boolean };
  offset_ms?: number;
  rtt_ms?: number;
  drift_ms?: number;
  event_id?: string | null;
  asset_id?: string | null;
  cache_ready?: boolean;
  cache_progress?: number;
  cache_missing?: number;
  storage_free_mb?: number;
}

export interface CalibrationProfile {
  id: string;
  device_id: string;
  name: string;
  config: Record<string, unknown>;
  active: boolean;
  created_at: number;
  updated_at: number;
}

export interface MediaAsset {
  id: string;
  title: string;
  kind: "video" | "audio" | "image";
  source: string;
  source_url: string;
  license: string;
  attribution: string;
  tags: string[];
  season: string;
  duration_ms: number;
  width: number;
  height: number;
  thumb_path: string;
  preview_path: string;
  rendition_path: string;
  sha256: string;
  size: number;
  suitability: { black_ratio?: number; mean_luma?: number; motion?: number; score?: number; edge_brightness?: number; framing_warning?: boolean };
  preset: Preset;
  status: string;
  error: string;
  created_at: number;
  technical?: { native_fps?: number; native?: {fps?: number}; delivery?: {fps?: number}; interpolation?: string; playback_verified?: boolean; [key: string]: unknown };
}

export interface SourcePack {
  id: string;
  name: string;
  files: number;
  scenes: number;
  characters: number;
  collections: string[];
  prepared: number;
  processing: number;
  errors: number;
  thumbnail_url: string;
}

export interface Preset {
  safe_margin?: number;
  mirror?: boolean;
  black_crush?: number;
  trim_start?: number;
  trim_end?: number;
  loop_crossfade?: number;
  loudnorm?: boolean;
  max_height?: number;
  fps?: number;
}

export type Pattern = "together" | "each" | "sequence" | "span" | "chase" | "ping-pong" | "random-one";
export type TransitionType =
  | "cut"
  | "dip"
  | "crossfade"
  | "wipe"
  | "cascade"
  | "ripple-out"
  | "ripple-in";

export interface Transition {
  type: TransitionType;
  duration: number;
  easing?: "linear" | "ease-in" | "ease-out" | "ease-in-out";
  direction?: "left" | "right" | "up" | "down";
  spread?: number;
  local?: "dip" | "crossfade" | "cut";
}

export interface Keyframe {
  t: number;
  x: number;
  y: number;
  w: number;
  h: number;
  opacity?: number;
}

export interface Cue {
  type: "cue" | "encounter";
  tracks?: { surface_id: string; asset_id: string; at: number; seconds?: number; audio?: boolean; handoff?: string; transition?: Transition }[];
  id: string;
  name?: string;
  clips: string[];
  pool?: { tags?: string[]; season?: string };
  target: { mode: "all" | "group" | "list"; group?: string; surfaces?: string[] };
  pattern: Pattern;
  direction?: "left-right" | "right-left" | "top-bottom" | "bottom-top" | "list";
  spacing?: { mode: "after" | "stagger" | "travel"; gap?: number; stagger?: number; speed?: number; exit_at?: number; enter_at?: number };
  duration?: { mode: "clip" | "fixed" | "loops"; seconds?: number; loops?: number };
  passes?: number;
  repeat?: number;
  hold?: number;
  fit?: "contain" | "cover" | "stretch";
  keyframes?: Keyframe[];
  transition?: Transition;
  offsets?: Record<string, number>;
  audio_surface_id?: string;
  audio_overrides?: Record<string, boolean>;
}

export interface Block {
  type: "block";
  id: string;
  name: string;
  repeat: number;
  shuffle?: boolean;
  items: Cue[];
}

export interface MarathonCharacter {
  id: string;
  name: string;
  clips: string[];
  source_program?: string;
}

export interface MarathonFamily {
  id: string;
  name: string;
  character_ids: string[];
  active_characters?: number;
}

export interface MarathonScheduler {
  type: "scheduler";
  id: string;
  name: string;
  roster: MarathonCharacter[];
  target: { mode: "all" | "group" | "list"; group?: string; surfaces?: string[] };
  recycle_min: number;
  recycle_max: number;
  gap_min: number;
  gap_max: number;
  rotation_gap_min: number;
  rotation_gap_max: number;
  active_characters?: number;
  surface_buffer?: number;
  motion_mode?: "auto" | "off";
  smart_choreography?: boolean;
  groups?: MarathonFamily[];
  fit?: "contain" | "cover" | "stretch";
  transition?: Transition;
}

export type ProgramItem = Cue | Block | MarathonScheduler;

export interface Program {
  id: string;
  name: string;
  loop_mode: "loop" | "once" | "shuffle_blocks";
  seed: number;
  items: ProgramItem[];
  updated_at: number;
}

export interface Schedule {
  id: string;
  program_id: string;
  days: number[];
  start: string;
  end: string;
  enabled: boolean;
}

export interface Slot {
  surface_id: string;
  device_ids: string[];
  start: number;
  end: number;
  asset_id: string;
  cue_id: string;
  cue_index: number;
  transition: Transition & { delay?: number };
  rect: Keyframe[];
  loop: boolean;
  flip_h?: boolean;
  motion_reason?: string;
}

export interface CompiledPreview {
  duration_ms: number;
  slots: Slot[];
  cues: { cue_id: string; name: string; start: number; end: number; item_path: number[] }[];
}

export interface RunnerState {
  state: "idle" | "syncing" | "playing" | "paused";
  program_id: string | null;
  program_name?: string;
  started_at?: number;
  now?: number;
  current?: { cue_id: string; name: string; start: number; end: number; details?: Record<string, unknown> } | null;
  next?: { cue_id: string; name: string; start: number; details?: Record<string, unknown> } | null;
  sync_progress?: Record<string, number>;
  message?: string;
  runtime_seed?: number | null;
  run_sheet?: {
    kind: "idle" | "marathon" | "shuffle" | "program";
    source_programs: string[];
    roster: { id: string; name: string; source_program: string; clip_count: number }[];
    active_characters: { id: string; name: string; source_program?: string; appearances_total: number; appearances_done: number }[];
    collection_group?: { id: string; name: string; complete: boolean } | null;
    character_bag_remaining: string[];
    next_rotation: number | null;
    movements: { start: number; end: number; state: "playing" | "upcoming"; surface_id: string; surface_name: string; asset_id: string; clip_name: string; character_id: string; character_name: string; source_program: string; cue_name: string; flip_h?: boolean; motion_reason?: string }[];
    upcoming_selections: { name: string; start: number; end: number; type: string }[];
  };
}

export interface CatalogItem {
  id: number;
  source: string;
  source_id: string;
  kind: string;
  title: string;
  description: string;
  page_url: string;
  media_url: string;
  thumb_url: string;
  preview_url: string;
  license: string;
  license_url: string;
  author: string;
  duration_ms: number;
  width: number;
  height: number;
  season: string;
  tags: string[];
  suitability: number;
  asset_id: string | null;
}

export interface ComfyParam {
  name: string;
  label: string;
  type: "string" | "text" | "int" | "float" | "image" | "seed";
  default: unknown;
  node: string;
  input: string;
}

export interface ComfyWorkflow {
  id: string;
  name: string;
  kind: string;
  description: string;
  params: ComfyParam[];
  builtin: boolean;
}

export interface ComfyJob {
  id: string;
  workflow_id: string;
  params: Record<string, unknown>;
  status: string;
  progress: number;
  asset_ids: string[];
  error: string;
  created_at: number;
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not json */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body?: unknown) => request<T>("POST", url, body),
  put: <T>(url: string, body?: unknown) => request<T>("PUT", url, body),
  patch: <T>(url: string, body?: unknown) => request<T>("PATCH", url, body),
  del: <T>(url: string) => request<T>("DELETE", url),
};

export const fileUrl = (p: string) => (p ? `/files/media/${p}` : "");

export function uid(): string {
  return Math.random().toString(36).slice(2, 10);
}

export function fmtDuration(ms: number): string {
  const s = Math.round(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}
