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
      theme: 'dark',
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
    { name: 'ae-ui-prefs' }
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
interface AuthStore {
  token: string | null;
  user: { account: string } | null;
  isAuthenticated: boolean;
  login: (token: string, account: string) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthStore>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      isAuthenticated: false,
      login: (token, account) => {
        localStorage.setItem('auth_token', token);
        set({ token, user: { account }, isAuthenticated: true });
      },
      logout: () => {
        localStorage.removeItem('auth_token');
        set({ token: null, user: null, isAuthenticated: false });
      },
    }),
    { name: 'ae-auth' }
  )
);

