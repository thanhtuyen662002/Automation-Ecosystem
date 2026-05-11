// ── WebSocket provider hook ───────────────────────────────────────────────────
import { useEffect, useRef } from 'react';
import { useWSStore, type LiveEvent } from './store';

const WS_URL = 'ws://localhost:8000/api/v1/ws/brain';

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
