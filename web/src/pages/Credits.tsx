import { useFetch } from "../hooks";

interface Credit {
  id: string;
  title: string;
  source: string;
  source_url: string;
  license: string;
  attribution: string;
}

export default function CreditsPage() {
  const { data = [] } = useFetch<Credit[]>("/api/credits", ["media"], []);
  return (
    <div className="col" style={{ gap: 16 }}>
      <h1>Credits</h1>
      <p className="muted">Attribution for media added from open sources. Keep this when sharing recordings of your display.</p>
      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>By</th>
              <th>License</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {data.map((c) => (
              <tr key={c.id}>
                <td>{c.title}</td>
                <td>{c.attribution}</td>
                <td>{c.license}</td>
                <td>
                  {c.source_url ? (
                    <a href={c.source_url} target="_blank" rel="noreferrer">
                      {c.source}
                    </a>
                  ) : (
                    c.source
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.length === 0 && <p className="muted">Nothing yet.</p>}
      </div>
    </div>
  );
}
