import { Link, NavLink, useLocation } from 'react-router-dom';
import { useEffect, useState, type ReactNode } from 'react';

import { he } from '../lib/he';

const RAIL_PATHS = ['/heatmap', '/integrity'];

type NavItem = {
  to: string;
  label: string;
  end?: boolean;
  icon: ReactNode;
  badge?: number | null;
};

function IconDashboard() {
  return (
    <svg className="sidebar__icon" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M4 13h7V4H4v9zm0 7h7v-5H4v5zm9 0h7V11h-7v9zm0-16v5h7V4h-7z"
        fill="currentColor"
      />
    </svg>
  );
}

function IconHeatmap() {
  return (
    <svg className="sidebar__icon" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M3 3h7v7H3V3zm11 0h7v7h-7V3zM3 14h7v7H3v-7zm11 0h7v7h-7v-7z"
        fill="currentColor"
        opacity="0.9"
      />
    </svg>
  );
}

function IconExplore() {
  return (
    <svg className="sidebar__icon" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="11" cy="11" r="6.5" stroke="currentColor" strokeWidth="2" />
      <path d="M16.5 16.5L21 21" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function IconInstallments() {
  return (
    <svg className="sidebar__icon" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M4 5h16v3H4V5zm0 5.5h16v3H4v-3zM4 16h10v3H4v-3z"
        fill="currentColor"
        opacity="0.95"
      />
    </svg>
  );
}

function IconCategorize() {
  return (
    <svg className="sidebar__icon" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 22 12 18.77 5.82 22 7 14.14l-5-4.87 6.91-1.01L12 2z"
        fill="currentColor"
      />
    </svg>
  );
}

function IconHoldings() {
  return (
    <svg className="sidebar__icon" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"
        fill="currentColor"
      />
    </svg>
  );
}

function IconPortfolio() {
  return (
    <svg className="sidebar__icon" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M3.5 18.5l6-6 4 4 7.5-8" stroke="currentColor" strokeWidth="2" fill="none" />
      <path d="M16 8h5v5" stroke="currentColor" strokeWidth="2" fill="none" />
    </svg>
  );
}

function IconPipeline() {
  return (
    <svg className="sidebar__icon" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M8 5v14l11-7L8 5z" fill="currentColor" />
    </svg>
  );
}

function IconSettings() {
  return (
    <svg className="sidebar__icon" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M19.14 12.94c.04-.31.06-.63.06-.94s-.02-.63-.06-.94l2.03-1.58a.49.49 0 00.12-.61l-1.92-3.32a.49.49 0 00-.59-.22l-2.39.96a7.2 7.2 0 00-1.62-.94l-.36-2.54A.48.48 0 0014 2h-4a.48.48 0 00-.48.41l-.36 2.54c-.59.24-1.13.55-1.62.94l-2.39-.96a.49.49 0 00-.59.22L2.25 8.87a.48.48 0 00.12.61l2.03 1.58c-.04.31-.07.63-.07.94s.03.63.07.94L2.37 14.5a.49.49 0 00-.12.61l1.92 3.32c.12.22.37.3.59.22l2.39-.96c.5.39 1.03.7 1.62.94l.36 2.54c.05.24.25.41.49.41h4c.24 0 .44-.17.49-.41l.36-2.54c.59-.24 1.13-.55 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32a.49.49 0 00-.12-.61l-2.01-1.58zM12 15.6A3.6 3.6 0 1112 8.4a3.6 3.6 0 010 7.2z"
        fill="currentColor"
      />
    </svg>
  );
}

function IconIntegrity() {
  return (
    <svg className="sidebar__icon" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm-2 16l-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9l-8 8z"
        fill="currentColor"
      />
    </svg>
  );
}

function pathWantsRail(pathname: string): boolean {
  return RAIL_PATHS.some((p) => pathname === p || pathname.startsWith(p + '/'));
}

export default function Sidebar({ categorizeBadge }: { categorizeBadge?: number | null }) {
  const loc = useLocation();
  const wantsRail = pathWantsRail(loc.pathname);
  const [forcedExpand, setForcedExpand] = useState(false);

  useEffect(() => {
    setForcedExpand(false);
  }, [loc.pathname]);

  const rail = wantsRail && !forcedExpand;

  useEffect(() => {
    const root = document.getElementById('app-shell');
    if (!root) return;
    root.classList.toggle('app-shell--rail', rail);
  }, [rail]);

  const daily: NavItem[] = [
    { to: '/', label: he.nav.dashboard, end: true, icon: <IconDashboard /> },
    { to: '/heatmap', label: he.nav.heatmap, icon: <IconHeatmap /> },
    { to: '/explore', label: he.nav.explore, icon: <IconExplore /> },
    { to: '/installments', label: he.nav.installments, icon: <IconInstallments /> },
    {
      to: '/categorize',
      label: he.nav.categorize,
      icon: <IconCategorize />,
      badge: categorizeBadge && categorizeBadge > 0 ? categorizeBadge : null,
    },
    { to: '/holdings', label: he.nav.holdings, icon: <IconHoldings /> },
    { to: '/portfolio', label: he.nav.portfolio, icon: <IconPortfolio /> },
  ];

  const ops: NavItem[] = [
    { to: '/pipeline', label: he.nav.pipeline, icon: <IconPipeline /> },
    { to: '/settings', label: he.nav.settings, icon: <IconSettings /> },
    { to: '/integrity', label: he.nav.integrity, icon: <IconIntegrity /> },
  ];

  const renderLink = (item: NavItem) => (
    <NavLink
      key={item.to}
      to={item.to}
      end={item.end}
      className={({ isActive }) => `sidebar__link${isActive ? ' active' : ''}`}
      title={item.label}
      aria-label={item.label}
    >
      {item.icon}
      <span className="sidebar__label">{item.label}</span>
      {item.badge != null ? <span className="sidebar__badge">{item.badge}</span> : null}
    </NavLink>
  );

  return (
    <aside className="sidebar" aria-label="Main">
      <Link to="/" className="sidebar__brand" title={he.app.wordmark}>
        {rail ? 'פ״ק' : he.app.wordmark}
      </Link>
      <nav className="sidebar__nav">{daily.map(renderLink)}</nav>
      <div className="sidebar__ops">
        <div className="sidebar__ops-title">{he.app.system}</div>
        {ops.map(renderLink)}
      </div>
      {wantsRail ? (
        <button
          type="button"
          className="sidebar__toggle"
          onClick={() => setForcedExpand((v) => !v)}
          title={forcedExpand ? he.app.collapseNav : he.app.expandNav}
        >
          {rail ? '«' : '»'}
        </button>
      ) : null}
    </aside>
  );
}
