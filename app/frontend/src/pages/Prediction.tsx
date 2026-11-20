import { useCallback, useEffect, useState } from 'react';

import PageShell from '../components/PageShell';
import { fetchJson, postJson, isAbortError } from '../lib/api';
import { he } from '../lib/he';

import './Prediction.css';

type PredictionItem = {
  category: string;
  confidence_pct: number;
};

type StatusResponse = {
  ok?: boolean;
  model_ready?: boolean;
  model_version?: string | null;
  has_embeddings?: boolean;
  minilm_present?: boolean;
  labeled_count?: number;
  awaiting_count?: number;
  message?: string;
};

type SampleRow = {
  fingerprint: string;
  date: string;
  payee: string;
  details: string;
  outflow: number;
  inflow: number;
  actual_category: string;
  predictions: PredictionItem[];
  top_category: string | null;
  top_confidence_pct: number | null;
  match: boolean | null;
};

type SampleResponse = {
  ok?: boolean;
  model_ready?: boolean;
  match_rate_pct?: number | null;
  match_count?: number;
  labeled_scored?: number;
  n?: number;
  rows?: SampleRow[];
  error?: string;
  message?: string;
};

type ScoreResponse = {
  ok?: boolean;
  predictions?: PredictionItem[];
  top_category?: string | null;
  top_confidence_pct?: number | null;
  message?: string;
  error?: string;
};

type TweakState = {
  fingerprint: string;
  payee: string;
  details: string;
  outflow: string;
  inflow: string;
  actual_category: string;
};

const emptyTweak = (): TweakState => ({
  fingerprint: '',
  payee: '',
  details: '',
  outflow: '0',
  inflow: '0',
  actual_category: '',
});

function fmtAmt(n: number): string {
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function PredList({ items }: { items: PredictionItem[] }) {
  if (!items.length) return <span className="pred-hint">{he.prediction.noPredictions}</span>;
  return (
    <ol className="pred-pred-list pred-pred-list--ranked">
      {items.map((p) => (
        <li key={p.category}>
          <span>{p.category}</span>
          <span>{Math.round(p.confidence_pct)}%</span>
        </li>
      ))}
    </ol>
  );
}

export default function Prediction() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [sampleN, setSampleN] = useState(20);
  const [labeledOnly, setLabeledOnly] = useState(true);
  const [sample, setSample] = useState<SampleResponse | null>(null);
  const [selectedFp, setSelectedFp] = useState<string | null>(null);
  const [tweak, setTweak] = useState<TweakState>(emptyTweak);
  const [score, setScore] = useState<ScoreResponse | null>(null);
  const [busySample, setBusySample] = useState(false);
  const [busyScore, setBusyScore] = useState(false);
  const [busyTrain, setBusyTrain] = useState(false);
  const [trainMsg, setTrainMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const loadStatus = useCallback(async (signal?: AbortSignal) => {
    try {
      const r = await fetchJson<StatusResponse>('/api/prediction/status', {
        cache: 'no-store',
        signal,
      });
      if (signal?.aborted) return;
      if (!r.ok) throw new Error(`status HTTP ${r.status}`);
      setStatus(r.data);
    } catch (e) {
      if (isAbortError(e)) return;
      setErr(e instanceof Error ? e.message : he.prediction.loadError);
    }
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    void loadStatus(ac.signal);
    return () => ac.abort();
  }, [loadStatus]);

  const runSample = useCallback(async () => {
    setBusySample(true);
    setErr(null);
    try {
      const qs = new URLSearchParams({
        n: String(sampleN),
        labeled_only: labeledOnly ? '1' : '0',
      });
      const r = await fetchJson<SampleResponse>(`/api/prediction/sample?${qs}`, {
        cache: 'no-store',
      });
      if (!r.ok) throw new Error(r.data.message || `sample HTTP ${r.status}`);
      setSample(r.data);
      setSelectedFp(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : he.prediction.loadError);
    } finally {
      setBusySample(false);
    }
  }, [sampleN, labeledOnly]);

  const loadIntoTweaker = useCallback((row: SampleRow) => {
    setSelectedFp(row.fingerprint);
    setTweak({
      fingerprint: row.fingerprint,
      payee: row.payee,
      details: row.details,
      outflow: String(row.outflow ?? 0),
      inflow: String(row.inflow ?? 0),
      actual_category: row.actual_category || '',
    });
    setScore({
      ok: true,
      predictions: row.predictions,
      top_category: row.top_category,
      top_confidence_pct: row.top_confidence_pct,
    });
  }, []);

  const runScore = useCallback(async () => {
    setBusyScore(true);
    setErr(null);
    try {
      const outflow = Number(tweak.outflow);
      const inflow = Number(tweak.inflow);
      const r = await postJson<ScoreResponse>('/api/prediction/score', {
        payee: tweak.payee,
        details: tweak.details,
        outflow: Number.isFinite(outflow) ? outflow : 0,
        inflow: Number.isFinite(inflow) ? inflow : 0,
        fingerprint: tweak.fingerprint || undefined,
        top_n: 5,
      });
      if (!r.ok) {
        throw new Error(r.data.message || r.data.error || `score HTTP ${r.status}`);
      }
      setScore(r.data);
    } catch (e) {
      setErr(e instanceof Error ? e.message : he.prediction.loadError);
    } finally {
      setBusyScore(false);
    }
  }, [tweak]);

  const runTrain = useCallback(async () => {
    setBusyTrain(true);
    setErr(null);
    setTrainMsg(null);
    try {
      const r = await postJson<StatusResponse & { trained?: boolean; message?: string }>(
        '/api/prediction/train',
        {},
      );
      if (!r.ok) {
        throw new Error(r.data.message || r.data.error || `train HTTP ${r.status}`);
      }
      setStatus(r.data);
      setTrainMsg(he.prediction.trainOk);
      setSample(null);
      setScore(null);
      setSelectedFp(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : he.prediction.trainFail);
    } finally {
      setBusyTrain(false);
    }
  }, []);

  const rows = sample?.rows ?? [];

  return (
    <PageShell title={he.prediction.title} sub={he.prediction.sub} width="wide">
      {err ? (
        <p className="pred-error" role="alert">
          {err}
        </p>
      ) : null}

      <section className="pred-card" aria-labelledby="pred-status-title">
        <h2 id="pred-status-title" className="pred-card-title">
          {he.prediction.status}
        </h2>
        <p className="pred-hint">{he.prediction.trainHint}</p>
        <dl className="pred-status-grid">
          <div>
            <dt>{he.prediction.status}</dt>
            <dd>
              {status?.model_ready ? (
                <span className="pred-pill pred-pill--ok">{he.prediction.modelReady}</span>
              ) : (
                <span className="pred-pill pred-pill--bad">{he.prediction.modelMissing}</span>
              )}
            </dd>
          </div>
          <div>
            <dt>{he.prediction.modelVersion}</dt>
            <dd>{status?.model_version || '—'}</dd>
          </div>
          <div>
            <dt>{he.prediction.embeddings}</dt>
            <dd>
              {status?.has_embeddings
                ? he.prediction.embeddingsOn
                : he.prediction.embeddingsOff}
            </dd>
          </div>
          <div>
            <dt>{he.prediction.minilmPresent}</dt>
            <dd>{status?.minilm_present ? '✓' : '—'}</dd>
          </div>
          <div>
            <dt>{he.prediction.labeled}</dt>
            <dd>{status?.labeled_count ?? '—'}</dd>
          </div>
          <div>
            <dt>{he.prediction.awaiting}</dt>
            <dd>{status?.awaiting_count ?? '—'}</dd>
          </div>
        </dl>
        <div className="pred-toolbar" style={{ marginTop: '0.85rem', marginBottom: 0 }}>
          <button
            type="button"
            className="pred-btn"
            disabled={busyTrain}
            onClick={() => void runTrain()}
          >
            {busyTrain ? he.prediction.training : he.prediction.train}
          </button>
          {trainMsg ? <span className="pred-meta">{trainMsg}</span> : null}
        </div>
      </section>

      <section className="pred-card" aria-labelledby="pred-sample-title">
        <h2 id="pred-sample-title" className="pred-card-title">
          {he.prediction.sampleTitle}
        </h2>
        <p className="pred-hint">{he.prediction.sampleHint}</p>
        <div className="pred-toolbar">
          <div className="pred-field">
            <label htmlFor="pred-n">{he.prediction.sampleN}</label>
            <input
              id="pred-n"
              type="number"
              min={1}
              max={100}
              value={sampleN}
              onChange={(e) => setSampleN(Number(e.target.value) || 1)}
            />
          </div>
          <div className="pred-field pred-field--check">
            <input
              id="pred-labeled"
              type="checkbox"
              checked={labeledOnly}
              onChange={(e) => setLabeledOnly(e.target.checked)}
            />
            <label htmlFor="pred-labeled">{he.prediction.labeledOnly}</label>
          </div>
          <button
            type="button"
            className="pred-btn"
            disabled={busySample}
            onClick={() => void runSample()}
          >
            {busySample ? he.prediction.sampling : he.prediction.sample}
          </button>
        </div>
        {sample ? (
          <p className="pred-meta">
            {he.prediction.matchRate}:{' '}
            {sample.match_rate_pct != null
              ? `${sample.match_rate_pct}% (${sample.match_count}/${sample.labeled_scored})`
              : '—'}
          </p>
        ) : null}
        {rows.length === 0 ? (
          <p className="pred-hint">{he.prediction.emptySample}</p>
        ) : (
          <div className="pred-table-wrap">
            <table className="pred-table">
              <thead>
                <tr>
                  <th>{he.prediction.date}</th>
                  <th>{he.prediction.payee}</th>
                  <th>{he.prediction.details}</th>
                  <th>{he.prediction.outflow}</th>
                  <th>{he.prediction.inflow}</th>
                  <th>{he.prediction.actual}</th>
                  <th>{he.prediction.predicted}</th>
                  <th>{he.prediction.confidence}</th>
                  <th>{he.prediction.matchYes}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.fingerprint}
                    className={[
                      'pred-row--clickable',
                      selectedFp === row.fingerprint ? 'pred-row--selected' : '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                    onClick={() => loadIntoTweaker(row)}
                  >
                    <td>{row.date || '—'}</td>
                    <td className="pred-clamp" title={row.payee}>
                      {row.payee || '—'}
                    </td>
                    <td className="pred-clamp" title={row.details}>
                      {row.details || '—'}
                    </td>
                    <td>{fmtAmt(row.outflow)}</td>
                    <td>{fmtAmt(row.inflow)}</td>
                    <td>{row.actual_category || '—'}</td>
                    <td>{row.top_category || '—'}</td>
                    <td>
                      {row.top_confidence_pct != null
                        ? `${Math.round(row.top_confidence_pct)}%`
                        : '—'}
                    </td>
                    <td>
                      {row.match == null ? (
                        '—'
                      ) : row.match ? (
                        <span className="pred-match-yes">{he.prediction.matchYes}</span>
                      ) : (
                        <span className="pred-match-no">{he.prediction.matchNo}</span>
                      )}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="pred-btn pred-btn--secondary pred-btn--small"
                        onClick={(e) => {
                          e.stopPropagation();
                          loadIntoTweaker(row);
                        }}
                      >
                        {he.prediction.useInTweaker}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="pred-card" aria-labelledby="pred-tweak-title">
        <h2 id="pred-tweak-title" className="pred-card-title">
          {he.prediction.tweakTitle}
        </h2>
        <p className="pred-hint">{he.prediction.tweakHint}</p>
        {tweak.actual_category ? (
          <p className="pred-meta">
            {he.prediction.actual}: <strong>{tweak.actual_category}</strong>
          </p>
        ) : null}
        <div className="pred-tweak-grid">
          <div className="pred-field">
            <label htmlFor="pred-payee">{he.prediction.payee}</label>
            <input
              id="pred-payee"
              type="text"
              value={tweak.payee}
              onChange={(e) => setTweak((t) => ({ ...t, payee: e.target.value }))}
            />
          </div>
          <div className="pred-field">
            <label htmlFor="pred-out">{he.prediction.outflow}</label>
            <input
              id="pred-out"
              type="number"
              step="0.01"
              value={tweak.outflow}
              onChange={(e) => setTweak((t) => ({ ...t, outflow: e.target.value }))}
            />
          </div>
          <div className="pred-field">
            <label htmlFor="pred-in">{he.prediction.inflow}</label>
            <input
              id="pred-in"
              type="number"
              step="0.01"
              value={tweak.inflow}
              onChange={(e) => setTweak((t) => ({ ...t, inflow: e.target.value }))}
            />
          </div>
          <div className="pred-field pred-field--wide">
            <label htmlFor="pred-details">{he.prediction.details}</label>
            <textarea
              id="pred-details"
              value={tweak.details}
              onChange={(e) => setTweak((t) => ({ ...t, details: e.target.value }))}
            />
          </div>
        </div>
        <div className="pred-toolbar">
          <button
            type="button"
            className="pred-btn"
            disabled={busyScore}
            onClick={() => void runScore()}
          >
            {busyScore ? he.prediction.scoring : he.prediction.score}
          </button>
          <button
            type="button"
            className="pred-btn pred-btn--secondary"
            onClick={() => {
              setTweak(emptyTweak());
              setScore(null);
              setSelectedFp(null);
            }}
          >
            {he.prediction.clearTweaker}
          </button>
        </div>
        <h3 className="pred-card-title" style={{ fontSize: '0.95rem', marginTop: '0.75rem' }}>
          {he.prediction.predictions}
        </h3>
        <PredList items={score?.predictions ?? []} />
      </section>
    </PageShell>
  );
}
