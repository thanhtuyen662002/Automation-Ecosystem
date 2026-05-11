/**
 * ui/src/lib/machine.ts — Machine ID utility (server-side only).
 *
 * SECURITY NOTE:
 * Machine fingerprinting is now computed ENTIRELY SERVER-SIDE by hashing
 * the client's IP + User-Agent + Accept-Language (+ optional X-Machine-ID header
 * injected by Electron from OS hardware UUID).
 *
 * The client MUST NOT generate or send a machine_id in the login request body
 * because any client-supplied value is untrusted and trivially spoofable.
 *
 * If this is running in Electron, set window.__MACHINE_ID__ from main.js using:
 *   const { execSync } = require('child_process');
 *   const id = execSync('wmic csproduct get uuid').toString().split('\n')[1].trim();
 *   mainWindow.webContents.executeJavaScript(`window.__MACHINE_ID__ = "${id}"`);
 *
 * The value is sent as the X-Machine-ID request header (see api.ts),
 * where the backend incorporates it into the HMAC fingerprint computation.
 * It is NOT used for any client-side logic.
 */

declare global {
  interface Window {
    __MACHINE_ID__?: string;
  }
}

/**
 * Returns the hardware UUID injected by Electron main.js, or empty string.
 * This value is sent as a header hint to the backend — the backend decides
 * whether to trust/use it. The client NEVER makes security decisions from it.
 */
export function getElectronMachineId(): string {
  if (typeof window !== 'undefined' && window.__MACHINE_ID__) {
    return window.__MACHINE_ID__;
  }
  return '';
}
