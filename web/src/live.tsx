import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, Device, RunnerState } from "./api";

interface LiveCtx {
  devices: Device[];
  runner: RunnerState;
  /** Bumped when the server says a topic changed, so pages can refetch. */
  versions: Record<string, number>;
  connected: boolean;
  toast: (msg: string, err?: boolean) => void;
  refreshDevices: () => void;
}

const Ctx = createContext<LiveCtx>(null as unknown as LiveCtx);

export function useLive() {
  return useContext(Ctx);
}

export function useTopic(topic: string): number {
  return useContext(Ctx).versions[topic] ?? 0;
}

export function LiveProvider({ children }: { children: ReactNode }) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [runner, setRunner] = useState<RunnerState>({ state: "idle", program_id: null });
  const [versions, setVersions] = useState<Record<string, number>>({});
  const [connected, setConnected] = useState(false);
  const [toastMsg, setToastMsg] = useState<{ msg: string; err: boolean } | null>(null);
  const toastTimer = useRef<number>();

  const toast = useCallback((msg: string, err = false) => {
    setToastMsg({ msg, err });
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastMsg(null), 4000);
  }, []);

  const refreshDevices = useCallback(() => {
    api.get<Device[]>("/api/devices").then(setDevices).catch(() => undefined);
  }, []);

  useEffect(() => {
    refreshDevices();
    api.get<RunnerState>("/api/runner").then(setRunner).catch(() => undefined);
    let ws: WebSocket | null = null;
    let stopped = false;
    let retry: number | undefined;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/ui`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!stopped) retry = window.setTimeout(connect, 2000);
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "devices") setDevices(msg.devices);
        else if (msg.type === "runner") setRunner(msg.state);
        else if (msg.type === "changed") setVersions((v) => ({ ...v, [msg.topic]: (v[msg.topic] ?? 0) + 1 }));
        else if (msg.type === "toast") toast(msg.message, !!msg.error);
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(retry);
      ws?.close();
    };
  }, [refreshDevices, toast]);

  return (
    <Ctx.Provider value={{ devices, runner, versions, connected, toast, refreshDevices }}>
      {children}
      {toastMsg && <div className={`toast ${toastMsg.err ? "err" : ""}`}>{toastMsg.msg}</div>}
    </Ctx.Provider>
  );
}
