// ── WebSocket provider hook ───────────────────────────────────────────────────
import { useEffect, useRef } from 'react';
import { useWSStore, type LiveEvent } from './store';

// Derive WS URL from current window location so it works in both:
//   dev  → wss?://localhost:5173/api/v1/ws/brain  (proxied by Vite → 8000)
//   prod → wss?://<real-host>/api/v1/ws/brain
const WS_URL = (() => {
  const configured = import.meta.env.VITE_WS_BASE || import.meta.env.VITE_API_BASE || '';
  if (configured) {
    const url = new URL(configured, window.location.origin);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    url.pathname = '/api/v1/ws/brain';
    url.search = '';
    return url.toString();
  }
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const host  = window.location.host;
  return `${proto}://${host}/api/v1/ws/brain`;
})();

let wsInstance: WebSocket | null = null;

export function useWebSocket() {
  const { setConnected, setClientCount, pushEvent } = useWSStore();
  const pingInterval = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (wsInstance && wsInstance.readyState === WebSocket.OPEN) return;

    const connect = () => {
      try {
        const ws = new WebSocket(WS_URL);
        wsInstance = ws;

        ws.onopen = () => {
          setConnected(true);
          pingInterval.current = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: 'ping' }));
            }
          }, 25000);
        };

        ws.onmessage = (msg) => {
          try {
            const payload = JSON.parse(msg.data);
            if (payload.event === 'ping' || payload.event === 'pong') {
              setClientCount(payload.data?.clients ?? 0);
              return;
            }
            const event: LiveEvent = {
              id: `${payload.event}-${Date.now()}`,
              event: payload.event,
              data: payload.data ?? {},
              ts: payload.ts ?? Date.now() / 1000,
            };
            pushEvent(event);
          } catch {}
        };

        ws.onclose = () => {
          setConnected(false);
          wsInstance = null;
          if (pingInterval.current) clearInterval(pingInterval.current);
          setTimeout(connect, 3000);
        };

        ws.onerror = () => {
          ws.close();
        };
      } catch {}
    };

    connect();

    return () => {
      if (pingInterval.current) clearInterval(pingInterval.current);
    };
  }, [setConnected, setClientCount, pushEvent]);
}
