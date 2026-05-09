"""
Identity Graph — Cross-device identity simulation.

Architecture contract:
  - All device pools are DETERMINISTIC: same account_id always produces the
    same pool. No random(), no process-level mutable state.
  - Device selection adds temporal bias (mobile → night, desktop → day) via
    stable_hash_int, not wall-clock randomness.
  - Network profiles are DERIVED from the selected device, never independent.
  - This module is PURELY advisory: it produces metadata consumed by callers
    (fingerprint generators, session controllers). It never mutates profiles.

Usage:
    from core.identity_graph import build_device_pool, select_active_device, derive_network_profile

    pool   = build_device_pool(account_id)
    device = select_active_device(account_id, now=int(time.time()))
    net    = derive_network_profile(device, now=int(time.time()))
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from core.mutation_controller import stable_hash_int, _account_noise

LOGGER = logging.getLogger("core.identity_graph")

# ── Constants ─────────────────────────────────────────────────────────────────

# Device type strings
MOBILE:  str = "mobile"
DESKTOP: str = "desktop"
TABLET:  str = "tablet"

# Pool-size distribution thresholds (cumulative probability * 1000)
_POOL_1_THRESH: int = 600   # 0–599  → 1 device  (60%)
_POOL_2_THRESH: int = 900   # 600–899 → 2 devices (30%)
#               900–999 → 3 devices (10%)

# Mobile-dominant device mix:
#   mobile  0–699  (70%)
#   desktop 700–899 (20%)
#   tablet  900–999 (10%)
_MOBILE_THRESH:  int = 700
_DESKTOP_THRESH: int = 900


# ── Imperfection helpers (Part 2) ─────────────────────────────────────────────

def _device_imperfection(account_id: str, now: int) -> bool:
    """~10% of sessions: pick an unexpected device instead of the weighted one.

    Models user picking up a secondary device (e.g. partner's tablet, work laptop).
    P4: keyed on HOURLY bucket (not per-minute) for session consistency.
    P8: key namespaced as 'device:flip_gate' — never reused by timing/persona/global.
    If device flips, network flip is suppressed to avoid unrealistic collision (P4/P9).
    """
    hour_bucket = now // 3600   # P4: hourly granularity
    return stable_hash_int(account_id, "device:flip_gate", str(hour_bucket)) % 10 == 0


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DeviceNode:
    """A single device in an account's identity graph."""
    device_id:     str
    device_type:   str          # mobile | desktop | tablet
    first_seen:    int          # Unix timestamp (deterministic, based on account age)
    last_seen:     int          # Unix timestamp (set on selection)
    usage_weight:  float        # 0.0–1.0; normalised across pool

    def __repr__(self) -> str:
        return f"DeviceNode({self.device_id[:8]} type={self.device_type} weight={self.usage_weight:.2f})"


@dataclass
class IdentityGraph:
    """The full multi-device identity for one account."""
    account_id:  str
    devices:     list[DeviceNode] = field(default_factory=list)

    def primary_device(self) -> DeviceNode | None:
        """Return the device with the highest usage weight."""
        return max(self.devices, key=lambda d: d.usage_weight) if self.devices else None


# ── Device pool construction ──────────────────────────────────────────────────

def _device_type_for(account_id: str, slot: int) -> str:
    """Deterministically assign a device type for a given pool slot."""
    v = stable_hash_int(account_id, "device_type", str(slot)) % 1000
    if v < _MOBILE_THRESH:
        return MOBILE
    if v < _DESKTOP_THRESH:
        return DESKTOP
    return TABLET


def build_device_pool(account_id: str) -> list[DeviceNode]:
    """Build the deterministic device pool for an account.

    Pool size distribution:
      60% → 1 device   (early adopters / single-device users)
      30% → 2 devices  (mobile + desktop workers)
      10% → 3 devices  (power users)

    Device type distribution (per slot, independent):
      70% mobile · 20% desktop · 10% tablet

    Returns a normalised list of DeviceNodes with usage_weight summing to 1.0.
    """
    v = stable_hash_int(account_id, "device_pool") % 1000
    if v < _POOL_1_THRESH:
        pool_size = 1
    elif v < _POOL_2_THRESH:
        pool_size = 2
    else:
        pool_size = 3

    # Deterministic "first seen" anchor: 0–179 days ago
    now     = int(time.time())
    age_raw = stable_hash_int(account_id, "device_age") % (180 * 86400)
    first_seen_base = now - age_raw

    nodes: list[DeviceNode] = []
    raw_weights: list[float] = []

    for i in range(pool_size):
        dtype    = _device_type_for(account_id, i)
        dev_id   = f"{account_id[:8]}-dev-{i}-{stable_hash_int(account_id, 'dev_id', str(i)) % 99999:05d}"
        # Each device has a slightly different first_seen
        fs       = first_seen_base + stable_hash_int(account_id, "dev_fs", str(i)) % (30 * 86400)
        # Raw weight: primary device (slot 0) always heavier
        raw_w    = 1.0 if i == 0 else 0.3 + (_account_noise(account_id, f"dev_w_{i}") * 0.4)
        nodes.append(DeviceNode(
            device_id=dev_id, device_type=dtype,
            first_seen=fs, last_seen=now,
            usage_weight=0.0,   # filled after normalisation
        ))
        raw_weights.append(raw_w)

    # Normalise weights
    total = sum(raw_weights)
    for node, w in zip(nodes, raw_weights):
        node.usage_weight = round(w / total, 4)

    LOGGER.debug(
        "identity_graph_pool account=%s size=%d devices=%s",
        account_id, pool_size, [(n.device_type, f"{n.usage_weight:.2f}") for n in nodes],
    )
    return nodes


# ── Active device selection ───────────────────────────────────────────────────

def select_active_device(account_id: str, now: int | None = None) -> DeviceNode:
    """Select the active device for this session, with time-of-day bias.

    Bias rules:
      - Mobile is preferred in evening/night (18:00–06:00 UTC-shifted)
      - Desktop is preferred during daytime (06:00–18:00 UTC-shifted)
      - Tablet follows mobile bias

    Selection is deterministic per (account_id, 15-minute window) so the
    device doesn't flicker mid-session.
    """
    if now is None:
        now = int(time.time())

    pool     = build_device_pool(account_id)
    hour     = (now // 3600) % 24
    is_night = hour >= 18 or hour < 6   # rough evening + night

    # Adjust weights by time-of-day
    adjusted: list[tuple[DeviceNode, float]] = []
    for node in pool:
        w = node.usage_weight
        if node.device_type == MOBILE and is_night:
            w *= 1.4
        elif node.device_type == DESKTOP and not is_night:
            w *= 1.3
        adjusted.append((node, w))

    # Deterministic weighted selection using 15-min window hash
    window  = now // 900
    slot    = stable_hash_int(account_id, "dev_select", str(window)) % 10000
    total   = sum(w for _, w in adjusted)
    cumsum  = 0.0
    selected = pool[0]  # fallback
    for node, w in adjusted:
        cumsum += w / total * 10000
        if slot < cumsum:
            selected = node
            break

    selected.last_seen = now
    # Track whether device was flipped for Part 4 network guard
    _flipped = False

    # Part 2 / 4: ~10% device imperfection - override with a random pool device.
    # If device flips, network flip is suppressed (Part 4 consistency guard).
    if len(pool) > 1 and _device_imperfection(account_id, now):
        flip_idx = stable_hash_int(account_id, "dev_flip_idx", str(now // 3600)) % len(pool)
        selected = pool[flip_idx]
        selected.last_seen = now
        _flipped = True
        LOGGER.debug("identity_graph_flip account=%s -> device=%s type=%s",
                     account_id, selected.device_id[:8], selected.device_type)

    # Attach flip flag so network profile derivation can read it
    selected._flipped = _flipped  # type: ignore[attr-defined]
    LOGGER.debug(
        "identity_graph_select account=%s device=%s type=%s hour=%d flipped=%s",
        account_id, selected.device_id[:8], selected.device_type, hour, _flipped,
    )
    return selected


# ── Network profile derivation ────────────────────────────────────────────────

# ASN buckets by region (coarse, deterministic)
_ASN_BUCKETS: list[str] = [
    "AS7922",   # Comcast (US)
    "AS3356",   # Level3  (US)
    "AS4837",   # China Unicom
    "AS9299",   # PLDT (PH)
    "AS4713",   # NTT (JP)
    "AS2516",   # KDDI (JP)
    "AS1273",   # BT (UK)
    "AS5089",   # Virgin Media (UK)
]

# P2: ASNs that map to mobile carrier networks (vs fixed-line ISPs).
_MOBILE_ASNS: frozenset[str] = frozenset({"AS9299", "AS4713", "AS2516"})  # PLDT, NTT, KDDI


def _enforce_identity_consistency(
    device_id: str,
    device_type: str,
    conn: str,
    asn: str,
    dev_flipped: bool,
    now: int,
) -> str:
    """P2: Probabilistic identity consistency guard.

    Corrects unrealistic device/network combinations:
      - mobile + wifi + non-mobile ASN → 80% corrected to mobile_data
      - desktop + mobile_data          → 85% corrected to wifi
    Soft correction (not hard override) — 20%/15% allow the anomaly
    to model real-world exceptions (tethering, hotspot, etc.).
    dev_flipped=True means network flip is already suppressed upstream; skip guard.
    Key namespaced under 'device:*' (P8).
    """
    if dev_flipped:
        return conn   # upstream already enforced consistency
    gate = stable_hash_int(device_id, "device:consistency_gate", str(now // 3600)) % 100
    if device_type == MOBILE and conn == "wifi" and asn not in _MOBILE_ASNS:
        # Mobile on fixed-line ISP + wifi is possible (home wifi) but uncommon.
        if gate < 80:
            LOGGER.debug("identity_consistency mobile+wifi→mobile_data device=%s", device_id[:8])
            return "mobile_data"
    if device_type == DESKTOP and conn == "mobile_data":
        # Desktop on cellular is unusual (tethering) — allow 15% through.
        if gate < 85:
            LOGGER.debug("identity_consistency desktop+mobile_data→wifi device=%s", device_id[:8])
            return "wifi"
    return conn

def derive_network_profile(device: DeviceNode, now: int | None = None) -> dict:
    """Derive network metadata from the selected device.

    Rules:
      mobile  → 70% mobile data, 30% WiFi
      desktop → 90% WiFi, 10% Ethernet
      tablet  → 60% WiFi, 40% mobile data

    ASN bucket and region are deterministically derived from device_id so
    the same device always appears on the same ISP segment.

    Returns:
        connection_type : "mobile_data" | "wifi" | "ethernet"
        asn             : coarse ISN string
        is_vpn          : False (never simulated as VPN — too risky)
        region_hint     : "ap" | "us" | "eu" (coarse)
    """
    if now is None:
        now = int(time.time())

    dtype = device.device_type
    roll  = stable_hash_int(device.device_id, "net_roll") % 100

    if dtype == MOBILE:
        conn = "mobile_data" if roll < 70 else "wifi"
    elif dtype == DESKTOP:
        conn = "wifi" if roll < 90 else "ethernet"
    else:  # tablet
        conn = "wifi" if roll < 60 else "mobile_data"

    asn_idx     = stable_hash_int(device.device_id, "asn") % len(_ASN_BUCKETS)
    asn         = _ASN_BUCKETS[asn_idx]
    region_idx  = stable_hash_int(device.device_id, "region") % 3
    region_hint = ["ap", "us", "eu"][region_idx]

    profile = {
        "connection_type": conn,
        "asn":             asn,
        "is_vpn":          False,
        "region_hint":     region_hint,
        "device_type":     dtype,
    }

    # P4: ~10% network inconsistency - flip wifi<->mobile_data.
    # P4 guard: skip if device already flipped this session (no simultaneous flip — P9).
    # P8: key namespaced as 'device:net_noise' — isolated from timing/persona/global layers.
    _dev_flipped = getattr(device, "_flipped", False)
    if not _dev_flipped and stable_hash_int(device.device_id, "device:net_noise", str(now // 60)) % 10 == 0:
        if conn == "wifi":
            profile["connection_type"] = "mobile_data"
        elif conn == "mobile_data":
            profile["connection_type"] = "wifi"
        LOGGER.debug("identity_graph_net_flip device=%s %s->%s",
                     device.device_id[:8], conn, profile["connection_type"])

    # P2: identity consistency guard — probabilistic correction of invalid combos.
    profile["connection_type"] = _enforce_identity_consistency(
        device.device_id, dtype, profile["connection_type"], asn, _dev_flipped, now
    )

    LOGGER.debug(
        "identity_graph_network device=%s conn=%s asn=%s region=%s",
        device.device_id[:8], profile["connection_type"], asn, region_hint,
    )
    return profile


# ── Convenience facade ────────────────────────────────────────────────────────

def get_identity_context(account_id: str, now: int | None = None) -> dict:
    """Return a complete identity context dict for injection into fingerprint generation.

    Keys:
        device_type       : mobile | desktop | tablet
        connection_type   : mobile_data | wifi | ethernet
        asn               : coarse ISN string
        region_hint       : ap | us | eu
        device_id         : opaque identifier (for logging/correlation)
    """
    if now is None:
        now = int(time.time())
    device = select_active_device(account_id, now)
    net    = derive_network_profile(device, now)
    return {
        "device_type":      device.device_type,
        "device_id":        device.device_id,
        "connection_type":  net["connection_type"],
        "asn":              net["asn"],
        "region_hint":      net["region_hint"],
    }
