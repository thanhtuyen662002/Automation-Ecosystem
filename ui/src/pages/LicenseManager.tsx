export function LicenseManager() {
  return (
    <div className="page">
      <div className="card" style={{ padding: '1.25rem' }}>
        <h2 style={{ marginTop: 0 }}>License administration moved to CLI</h2>
        <p style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          The old browser license manager was removed because license tables are service-role only.
          Use the backend admin scripts to create, renew, revoke, list, and reset license devices.
        </p>
      </div>
    </div>
  );
}
