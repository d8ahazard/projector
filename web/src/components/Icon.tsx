const paths = {
  live: "M8 5v14l11-7z", library: "M3 5h18v14H3zM7 9h.01M3 16l5-5 4 4 3-3 6 6",
  programs: "M8 4h13M8 12h13M8 20h13M3 4h.01M3 12h.01M3 20h.01",
  calibrate: "M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5M8 8h8v8H8z",
  devices: "M3 4h18v12H3zM8 21h8m-4-5v5", generate: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z",
  layout: "M3 3h7v9H3zM14 3h7v5h-7zM3 16h7v5H3zM14 12h7v9h-7z",
  settings: "M4 7h16M4 17h16M8 4v6m8 4v6", catalog: "M21 21l-5-5M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0",
  more: "M5 12h.01M12 12h.01M19 12h.01", arrow: "M4 12h16m-6-6 6 6-6 6",
  plus: "M12 5v14M5 12h14", close: "m6 6 12 12M6 18 18 6", check: "m5 12 4 4L19 6",
  qr: "M3 3h6v6H3zM15 3h6v6h-6zM3 15h6v6H3zM15 15h3v3h3v3h-6z",
  phone: "M7 2h10v20H7zM11 18h2", wifi: "M2 8a16 16 0 0 1 20 0M5 12a11 11 0 0 1 14 0m-11 4a6 6 0 0 1 8 0m-4 4h.01",
} as const;
export type IconName = keyof typeof paths;
export default function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
