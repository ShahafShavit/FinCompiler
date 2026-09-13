import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

import PageShell from './components/PageShell';
import Sidebar from './components/Sidebar';
import { he } from './lib/he';
import { openCountFromSummary, useCategorizeSummary } from './lib/useCategorizeSummary';
import Categorize from './pages/Categorize';
import Cash from './pages/Cash';
import Dashboard from './pages/Dashboard';
import DataIntegrity from './pages/DataIntegrity';
import Explore from './pages/Explore';
import Heatmap from './pages/Heatmap';
import HeatmapDetail from './pages/HeatmapDetail';
import Holdings from './pages/Holdings';
import Installments from './pages/Installments';
import Portfolio from './pages/Portfolio';
import Pipeline from './pages/Pipeline';
import Settings from './pages/Settings';

type EBState = { error: Error | null };

class ErrorBoundary extends Component<{ children: ReactNode }, EBState> {
  state: EBState = { error: null };
  static getDerivedStateFromError(error: Error): EBState {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[App ErrorBoundary]', error, info);
  }
  render() {
    if (this.state.error) {
      return (
        <PageShell title={he.app.renderError} width="narrow">
          <div className="card eb-wrap">
            <p className="banner banner--err" role="alert">
              {he.app.renderError}
            </p>
            <button
              type="button"
              onClick={() => {
                this.setState({ error: null });
                window.location.assign('/');
              }}
            >
              {he.app.retry}
            </button>
          </div>
        </PageShell>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  const summaryQ = useCategorizeSummary();
  const badge = openCountFromSummary(summaryQ.data);
  return (
    <div id="app-shell" className="app-shell">
      <Sidebar categorizeBadge={badge} />
      <div className="app-main">
        <ErrorBoundary>
          <Routes>
            {/* One path per page — do not pair `/foo` + `/foo/` Navigate redirects;
                React Router treats them as the same match and that loops forever. */}
            <Route path="/" element={<Dashboard />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/pipeline" element={<Pipeline />} />
            <Route path="/heatmap" element={<Heatmap />} />
            <Route path="/heatmap/detail" element={<HeatmapDetail />} />
            <Route path="/explore" element={<Explore />} />
            <Route path="/installments" element={<Installments />} />
            <Route path="/integrity" element={<DataIntegrity />} />
            <Route path="/categorize" element={<Categorize />} />
            <Route path="/cash" element={<Cash />} />
            <Route path="/holdings" element={<Holdings />} />
            <Route path="/portfolio" element={<Portfolio />} />
            <Route path="/heatmap/index.html" element={<Navigate to="/heatmap" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </ErrorBoundary>
      </div>
    </div>
  );
}
