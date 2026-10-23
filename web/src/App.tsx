import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import LayoutPage from "./pages/Layout";
import DevicesPage from "./pages/Devices";
import LibraryPage from "./pages/Library";
import CatalogPage from "./pages/Catalog";
import ProgramsPage from "./pages/Programs";
import CalibratePage from "./pages/Calibrate";
import GeneratePage from "./pages/Generate";
import SettingsPage from "./pages/Settings";
import CreditsPage from "./pages/Credits";
import SetupPage from "./pages/Setup";
import MovementPage from "./pages/Movement";
import { LiveProvider, useLive } from "./live";
import Icon, { IconName } from "./components/Icon";

const NAV = [
  ["/", "Live"],
  ["/programs", "Programs"],
  ["/library", "Library"],
  ["/catalog", "Catalog"],
  ["/movement", "Movement Lab"],
  ["/generate", "Generate"],
  ["/layout", "Layout"],
  ["/devices", "Devices"],
  ["/calibrate", "Calibrate"],
  ["/settings", "Settings"],
] as const;

const icons: IconName[] = ["live", "programs", "library", "catalog", "library", "generate", "layout", "devices", "calibrate", "settings"];
function Shell() {
  const { connected, devices } = useLive();
  const [more, setMore] = useState(false);
  const location = useLocation();
  useEffect(() => { setMore(false); window.scrollTo(0, 0); }, [location.pathname]);
  const online = devices.filter(d => d.role === "player" && d.online).length;
  const brand = <><span className="brand-mark"><Icon name="live" size={23} /></span><span>Projector<small>CONTROL STUDIO</small></span></>;
  return (
      <div className="shell">
        <header className="mobile-header"><div className="brand">{brand}</div><span className={`connection-pill ${connected ? "online" : ""}`}><span className={`dot ${connected ? "on" : ""}`} />{connected ? "Connected" : "Reconnecting"}</span></header>
        <nav className="nav" aria-label="Main navigation">
          <div className="brand">{brand}</div>
          <div className="nav-caption">YOUR SHOW</div>
          {NAV.map(([to, label], i) => (
            <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
              <Icon name={icons[i]} />{label}
            </NavLink>
          ))}
          <div className="nav-health"><span className={`dot ${connected ? "on" : ""}`} /><div>{connected ? "Server connected" : "Connecting to server"}<small>{online} {online === 1 ? "player" : "players"} online</small></div></div>
        </nav>
        <main className="main">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/programs" element={<ProgramsPage />} />
            <Route path="/programs/:id" element={<ProgramsPage />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/catalog" element={<CatalogPage />} />
            <Route path="/movement" element={<MovementPage />} />
            <Route path="/generate" element={<GeneratePage />} />
            <Route path="/layout" element={<LayoutPage />} />
            <Route path="/devices" element={<DevicesPage />} />
            <Route path="/devices/:id" element={<DevicesPage />} />
            <Route path="/devices/:id/setup" element={<SetupPage />} />
            <Route path="/setup" element={<SetupPage />} />
            <Route path="/calibrate" element={<CalibratePage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/credits" element={<CreditsPage />} />
          </Routes>
        </main>
        <nav className="mobile-dock" aria-label="Mobile navigation">
          {([0, 2, 1, 8] as const).map(i => <NavLink key={NAV[i][0]} to={NAV[i][0]} end={i === 0}><Icon name={icons[i]} /><span>{NAV[i][1]}</span></NavLink>)}
          <button className={more ? "active" : ""} aria-expanded={more} aria-controls="more-navigation" onClick={() => setMore(!more)}><Icon name="more" /><span>More</span></button>
        </nav>
        {more && <div className="menu-scrim" onClick={() => setMore(false)}><nav id="more-navigation" className="more-sheet" aria-label="More navigation" onClick={e => e.stopPropagation()}><div className="row"><h2>Control studio</h2><button className="icon-button right" onClick={() => setMore(false)} aria-label="Close menu"><Icon name="close" /></button></div><div className="more-grid">{NAV.map(([to, label], i) => <NavLink key={to} to={to} end={to === "/"}><Icon name={icons[i]} />{label}</NavLink>)}</div></nav></div>}
      </div>
  );
}
export default function App() { return <LiveProvider><Shell /></LiveProvider>; }
