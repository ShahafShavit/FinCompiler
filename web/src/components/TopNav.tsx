import { NavLink } from 'react-router-dom';

export default function TopNav() {
  return (
    <nav className="topnav" aria-label="Main">
      <NavLink to="/" end>
        Dashboard
      </NavLink>
      <span className="sep">·</span>
      <NavLink to="/pipeline">Pipeline</NavLink>
      <span className="sep">·</span>
      <NavLink to="/heatmap">Heatmap</NavLink>
      <span className="sep">·</span>
      <NavLink to="/integrity">Data integrity</NavLink>
      <span className="sep">·</span>
      <a href="/holdings/">Holdings</a>
      <span className="sep">·</span>
      <a href="/categorize/">Categorize</a>
    </nav>
  );
}
