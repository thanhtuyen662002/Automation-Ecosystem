export {};

declare global {
  interface Window {
    automationDesktop?: {
      platform: string;
      apiBaseUrl: string;
      getConfig?: () => Promise<{ apiBaseUrl: string; logFile: string }>;
      onStartupStatus?: (callback: (payload: { title?: string; detail?: string }) => void) => () => void;
    };
  }
}
