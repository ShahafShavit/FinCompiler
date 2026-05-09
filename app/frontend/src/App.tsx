import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

import TopNav from './components/TopNav';
import Dashboard from './pages/Dashboard';
import DataIntegrity from './pages/DataIntegrity';
import Pipeline from './pages/Pipeline';
import Heatmap from './pages/Heatmap';
import HeatmapDetail from './pages/HeatmapDetail';
import Categorize from './pages/Categorize';
import Holdings from './pages/Holdings';

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
        <div style={{ padding: '1rem', color: '#ffb4b4', fontFamily: 'monospace' }}>
          <h2>Render error</h2>
          <pre style={{ whiteSpace: 'pre-wrap' }}>
            {this.state.error.name}: {this.state.error.message}
            {'\n\n'}
            {this.state.error.stack}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  return (
    <>
      <TopNav />
      <ErrorBoundary>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/pipeline" element={<Pipeline />} />
          <Route path="/heatmap" element={<Heatmap />} />
          <Route path="/heatmap/detail" element={<HeatmapDetail />} />
          <Route path="/integrity" element={<DataIntegrity />} />
          <Route path="/categorize" element={<Navigate to="/categorize/" replace />} />
          <Route path="/categorize/" element={<Categorize />} />
          <Route path="/holdings" element={<Navigate to="/holdings/" replace />} />
          <Route path="/holdings/" element={<Holdings />} />
          <Route path="/heatmap/index.html" element={<Navigate to="/heatmap" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </ErrorBoundary>
    </>
  );
}
