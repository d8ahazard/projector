import { useState } from "react";
import { Link } from "react-router-dom";
import { api, CatalogItem, fmtDuration } from "../api";
import { useFetch } from "../hooks";
import { useLive } from "../live";

interface Status {
  running: boolean;
  done: number;
  total: number;
  added: number;
  errors: string[];
  current: string;
  finished_at: number | null;
  sources: Record<string, boolean>;
  seasons: string[];
  downloads: Record<string, { state: string; progress: number; error: string; asset_id?: string }>;
}

const SOURCE_LABELS: Record<string, string> = {
  archive: "Internet Archive",
  wikimedia: "Wikimedia Commons",
  pexels: "Pexels",
  pixabay: "Pixabay",
  freesound: "Freesound",
};

const PAGE = 60;

export default function CatalogPage() {
  const { toast } = useLive();
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [season, setSeason] = useState("halloween");
  const [source, setSource] = useState("");
  const [kind, setKind] = useState("video");
  const [sort, setSort] = useState("suitability");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<CatalogItem | null>(null);
  const params = new URLSearchParams({ q: query, season, source, kind, sort, limit: String(PAGE), offset: String(page * PAGE) });
  const { data } = useFetch<{ items: CatalogItem[]; total: number }>(`/api/catalog?${params}`, ["catalog"]);
  const { data: status } = useFetch<Status>("/api/catalog/status", ["catalog_status", "catalog_downloads"]);

  const index = async (queries: string[] = []) => {
    try {
      await api.post("/api/catalog/index", { seasons: season ? [season] : ["halloween", "christmas"], queries });
      toast(queries.length ? `Searching sources for "${queries[0]}"…` : "Indexing started; results appear as they arrive.");
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const add = async (it: CatalogItem) => {
    try {
      await api.post(`/api/catalog/${it.id}/add`, { season: it.season, tags: [] });
      toast(`Downloading "${it.title}"…`);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const dl = (id: number) => status?.downloads[String(id)];

  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="row">
        <h1 style={{ margin: 0 }}>Catalog</h1>
        <span className="muted small">Free and openly licensed seasonal videos and sounds. Add to the library, then tune them there.</span>
      </div>

      <div className="panel col">
        <div className="row">
          <button className="primary" disabled={status?.running} onClick={() => index()}>
            {status?.running ? "Indexing…" : total ? "Refresh catalog" : "Build catalog"}
          </button>
          {status && (
            <span className="row small" style={{ gap: 6 }}>
              {Object.entries(status.sources).map(([s, ok]) => (
                <span key={s} className="tag" title={ok ? "enabled" : "needs an API key in Settings"} style={{ opacity: ok ? 1 : 0.45 }}>
                  {SOURCE_LABELS[s] ?? s}
                  {!ok && " (key)"}
                </span>
              ))}
            </span>
          )}
          {status && Object.values(status.sources).some((v) => !v) && (
            <Link className="small right" to="/settings">
              Add API keys
            </Link>
          )}
        </div>
        {status?.running && (
          <div className="col" style={{ gap: 4 }}>
            <div className="progress">
              <div style={{ width: `${(status.done / Math.max(1, status.total)) * 100}%` }} />
            </div>
            <span className="small muted">
              {status.done}/{status.total} searches · {status.added} new · {status.current}
            </span>
          </div>
        )}
        {status && !status.running && status.errors.length > 0 && (
          <details className="small muted">
            <summary>{status.errors.length} search errors in the last run</summary>
            {status.errors.map((e, i) => (
              <div key={i}>{e}</div>
            ))}
          </details>
        )}
      </div>

      <div className="row">
        <input
          placeholder="Search the catalog…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              setQuery(q);
              setPage(0);
            }
          }}
          style={{ minWidth: 240 }}
        />
        <button
          onClick={() => {
            setQuery(q);
            setPage(0);
          }}
        >
          Filter
        </button>
        <button disabled={!q.trim() || status?.running} title="Query every source for this term and add results to the catalog" onClick={() => index([q.trim()])}>
          Search sources
        </button>
        <select value={season} onChange={(e) => (setSeason(e.target.value), setPage(0))}>
          <option value="halloween">Halloween</option>
          <option value="christmas">Christmas</option>
          <option value="">Any season</option>
        </select>
        <select value={kind} onChange={(e) => (setKind(e.target.value), setPage(0))}>
          <option value="video">Video</option>
          <option value="audio">Audio</option>
          <option value="">All</option>
        </select>
        <select value={source} onChange={(e) => (setSource(e.target.value), setPage(0))}>
          <option value="">All sources</option>
          {Object.entries(SOURCE_LABELS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="suitability">Best for windows</option>
          <option value="newest">Recently indexed</option>
          <option value="title">Title</option>
          <option value="duration">Shortest</option>
        </select>
        <span className="muted small right">{total} items</span>
      </div>

      <div className="split">
        <div className="col">
          {items.length === 0 && <div className="panel muted">Nothing here yet. Build the catalog to search all sources.</div>}
          <div className="grid cards">
            {items.map((it) => {
              const d = dl(it.id);
              return (
                <div key={it.id} className="card" style={selected?.id === it.id ? { borderColor: "var(--accent)" } : undefined} onClick={() => setSelected(it)}>
                  <div className="thumb" style={{ backgroundImage: it.thumb_url ? `url(${it.thumb_url})` : undefined }}>
                    {it.duration_ms > 0 && <span className="badge">{fmtDuration(it.duration_ms)}</span>}
                    {it.asset_id && (
                      <span className="badge" style={{ left: 6, right: "auto", color: "var(--ok)" }}>
                        in library
                      </span>
                    )}
                  </div>
                  <div className="body">
                    <div className="title" title={it.title}>
                      {it.title}
                    </div>
                    <div className="row small muted" style={{ gap: 4 }}>
                      <span className="tag">{SOURCE_LABELS[it.source] ?? it.source}</span>
                      <span className="tag" title="Estimated suitability for window projection">
                        fit {Math.round(it.suitability * 100)}
                      </span>
                    </div>
                    {d?.state === "downloading" ? (
                      <div className="progress" style={{ marginTop: 6 }}>
                        <div style={{ width: `${d.progress * 100}%` }} />
                      </div>
                    ) : !it.asset_id ? (
                      <button
                        className="small"
                        style={{ marginTop: 6 }}
                        onClick={(e) => {
                          e.stopPropagation();
                          add(it);
                        }}
                      >
                        + Add to library
                      </button>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
          {total > PAGE && (
            <div className="row">
              <button disabled={page === 0} onClick={() => setPage(page - 1)}>
                ‹ Prev
              </button>
              <span className="small muted">
                Page {page + 1} of {Math.ceil(total / PAGE)}
              </span>
              <button disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>
                Next ›
              </button>
            </div>
          )}
        </div>

        <div className="panel" style={{ alignSelf: "flex-start", position: "sticky", top: 12 }}>
          {selected ? (
            <div className="col">
              <b>{selected.title}</b>
              {selected.kind === "video" && selected.preview_url ? (
                <video src={selected.preview_url} controls muted loop style={{ width: "100%", background: "#000" }} />
              ) : selected.kind === "video" && selected.media_url ? (
                <video src={selected.media_url} controls muted preload="metadata" style={{ width: "100%", background: "#000" }} />
              ) : selected.kind === "audio" && selected.media_url ? (
                <audio src={selected.media_url} controls style={{ width: "100%" }} />
              ) : selected.thumb_url ? (
                <img src={selected.thumb_url} style={{ width: "100%" }} />
              ) : null}
              <div className="small">
                <div>
                  <span className="muted">License: </span>
                  {selected.license_url ? (
                    <a href={selected.license_url} target="_blank" rel="noreferrer">
                      {selected.license || "see source"}
                    </a>
                  ) : (
                    selected.license || "see source"
                  )}
                </div>
                {selected.author && (
                  <div>
                    <span className="muted">By: </span>
                    {selected.author}
                  </div>
                )}
                {selected.width > 0 && (
                  <div>
                    <span className="muted">Size: </span>
                    {selected.width}×{selected.height}
                  </div>
                )}
              </div>
              {selected.description && (
                <div className="small muted" style={{ maxHeight: 160, overflowY: "auto" }}>
                  {selected.description}
                </div>
              )}
              <div className="row small" style={{ gap: 4 }}>
                {selected.tags.slice(0, 12).map((t) => (
                  <span key={t} className="tag">
                    {t}
                  </span>
                ))}
              </div>
              {dl(selected.id)?.state === "error" && <div className="small" style={{ color: "var(--bad)" }}>{dl(selected.id)?.error}</div>}
              <div className="row">
                <a href={selected.page_url} target="_blank" rel="noreferrer" className="small">
                  View source page
                </a>
                {selected.asset_id ? (
                  <Link to="/library" className="right">
                    Open in library
                  </Link>
                ) : (
                  <button className="primary right" disabled={dl(selected.id)?.state === "downloading"} onClick={() => add(selected)}>
                    Add to library
                  </button>
                )}
              </div>
              <button
                className="small"
                onClick={async () => {
                  await api.del(`/api/catalog/${selected.id}`);
                  setSelected(null);
                  toast("Hidden from catalog");
                }}
              >
                Hide from catalog
              </button>
            </div>
          ) : (
            <div className="muted small">
              Select an item to preview it. Clips on a black background score highest because they look like they're floating in the window.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
