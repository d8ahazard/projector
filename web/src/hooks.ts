import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useLive, useTopic } from "./live";

/** Fetches `url`, refetching whenever any of the given live topics change. */
export function useFetch<T>(url: string | null, topics: string[] = [], initial?: T) {
  const [data, setData] = useState<T | undefined>(initial);
  const [error, setError] = useState<string>("");
  const { versions } = useLive();
  const key = topics.map((t) => versions[t] ?? 0).join(",");
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);
  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    api
      .get<T>(url)
      .then((d) => !cancelled && (setData(d), setError("")))
      .catch((e) => !cancelled && setError(String(e.message ?? e)));
    return () => {
      cancelled = true;
    };
  }, [url, key, nonce]);
  return { data, setData, error, reload };
}

/** Calls fn at most every `ms`, always delivering the latest arguments. */
export function useThrottled<A extends unknown[]>(fn: (...args: A) => void, ms = 80) {
  const last = useRef(0);
  const pending = useRef<A | null>(null);
  const timer = useRef<number>();
  const fnRef = useRef(fn);
  fnRef.current = fn;
  return useCallback(
    (...args: A) => {
      const now = Date.now();
      pending.current = args;
      const fire = () => {
        last.current = Date.now();
        const a = pending.current;
        pending.current = null;
        if (a) fnRef.current(...a);
      };
      if (now - last.current >= ms) fire();
      else {
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(fire, ms - (now - last.current));
      }
    },
    [ms],
  );
}

export { useTopic };
