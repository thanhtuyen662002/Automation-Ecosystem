// ── Stores ───────────────────────────────────────────────────────────────────
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

// ── Theme Store ───────────────────────────────────────────────────────────────
type Theme = 'dark' | 'light' | 'neon';
type Language = 'en' | 'vi';

interface UIStore {
  theme: Theme;
  language: Language;
  sidebarCollapsed: boolean;
  pendingCount: number;
  executionEnabled: boolean;
  autoApprove: boolean;
  setTheme: (t: Theme) => void;
  setLanguage: (l: Language) => void;
  toggleSidebar: () => void;
  setPendingCount: (n: number) => void;
  setExecutionEnabled: (v: boolean) => void;
  setAutoApprove: (v: boolean) => void;
}

export const useUIStore = create<UIStore>()(
  persist(
    (set) => ({
      theme: 'light',
      language: 'en',
      sidebarCollapsed: false,
      pendingCount: 3,
      executionEnabled: true,
      autoApprove: false,
      setTheme: (theme) => {
        set({ theme });
        document.documentElement.setAttribute('data-theme', theme);
      },
      setLanguage: (language) => set({ language }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setPendingCount: (pendingCount) => set({ pendingCount }),
      setExecutionEnabled: (executionEnabled) => set({ executionEnabled }),
      setAutoApprove: (autoApprove) => set({ autoApprove }),
    }),
    {
      name: 'ae-ui-prefs',
      version: 2,
      // v1→v2: reset old 'dark' default to 'light' (Glassmorp redesign)
      migrate: (state: any, version: number) => {
        if (version < 2) return { ...state, theme: 'light' };
        return state;
      },
    }
  )
);

// ── WebSocket / Live Feed Store ───────────────────────────────────────────────
export interface LiveEvent {
  id: string;
  event: 'decision_made' | 'publish_event' | 'metric_update' | 'ping' | 'connected';
  data: Record<string, unknown>;
  ts: number;
}

interface WSStore {
  connected: boolean;
  clientCount: number;
  events: LiveEvent[];
  setConnected: (c: boolean) => void;
  setClientCount: (n: number) => void;
  pushEvent: (e: LiveEvent) => void;
  clearEvents: () => void;
}

export const useWSStore = create<WSStore>()((set) => ({
  connected: false,
  clientCount: 0,
  events: [],
  setConnected: (connected) => set({ connected }),
  setClientCount: (clientCount) => set({ clientCount }),
  pushEvent: (e) => set((s) => ({ events: [e, ...s.events].slice(0, 100) })),
  clearEvents: () => set({ events: [] }),
}));

// ── Auth Store ────────────────────────────────────────────────────────────────

