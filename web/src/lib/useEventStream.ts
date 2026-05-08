import { useEffect, useRef, useState } from 'react';

export type SseConnState = 'connecting' | 'open' | 'closed';

export type SseHandlers = {
  log?: (msg: string) => void;
  state?: (running: boolean, job: string, error?: string | null) => void;
};

export function useEventStream(url: string, handlers: SseHandlers): SseConnState {
  const [conn, setConn] = useState<SseConnState>('connecting');
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    const es = new EventSource(url);
    es.onopen = () => setConn('open');
    es.onerror = () => setConn('closed');
    es.addEventListener('log', (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        if (handlersRef.current.log) handlersRef.current.log(String(d.message || ''));
      } catch {
        // ignore malformed events
      }
    });
    es.addEventListener('state', (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        if (handlersRef.current.state) {
          handlersRef.current.state(!!d.running, String(d.job || ''), d.error ?? null);
        }
      } catch {
        // ignore malformed events
      }
    });
    return () => {
      es.close();
    };
  }, [url]);

  return conn;
}
