export type LicenseStatus =
  | 'active'
  | 'active_offline'
  | 'not_activated'
  | 'invalid_key'
  | 'expired'
  | 'suspended'
  | 'revoked'
  | 'device_revoked'
  | 'already_activated_on_another_device'
  | 'machine_mismatch'
  | 'verification_required'
  | 'network_error'
  | 'server_error';

export interface LicenseInfo {
  id?: string;
  label?: string | null;
  plan?: string | null;
  expires_at?: string | null;
  is_lifetime?: boolean;
  max_devices?: number;
}

export interface LicenseDeviceInfo {
  id?: string;
  status?: string;
  last_seen_at?: string | null;
  device_name?: string | null;
  platform?: string | null;
  app_version?: string | null;
  machine_id_hash?: string | null;
}

export interface LicenseResponse {
  ok: boolean;
  licensed: boolean;
  status: LicenseStatus;
  reason: string | null;
  license: LicenseInfo | null;
  device: LicenseDeviceInfo | null;
  offline_valid_until: string | null;
}
