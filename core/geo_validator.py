"""
Geo consistency validation — no external API, uses timezone/locale lookup tables.

Detects when an account's browser fingerprint (timezone + locale) is inconsistent
with its proxy country. Mismatches are a strong ban signal: platforms cross-check
IP geolocation against browser timezone.

Usage:
    issues = check_geo_consistency("Asia/Ho_Chi_Minh", "vi-VN", "VN")
    # issues = []  (consistent — VN in all three)

    issues = check_geo_consistency("America/New_York", "vi-VN", "VN")
    # issues = ["timezone country US != proxy country VN", ...]
"""
from __future__ import annotations

import logging

LOGGER = logging.getLogger("core.geo_validator")

# ── Timezone → ISO-3166-1 alpha-2 country mapping ───────────────────────────
# Covers all major timezones likely to be used in automated publishing systems.
TIMEZONE_COUNTRY: dict[str, str] = {
    # Americas
    "America/New_York":        "US", "America/Chicago":       "US",
    "America/Denver":          "US", "America/Los_Angeles":   "US",
    "America/Phoenix":         "US", "America/Anchorage":     "US",
    "America/Honolulu":        "US", "America/Toronto":       "CA",
    "America/Vancouver":       "CA", "America/Winnipeg":      "CA",
    "America/Halifax":         "CA", "America/St_Johns":      "CA",
    "America/Mexico_City":     "MX", "America/Monterrey":     "MX",
    "America/Sao_Paulo":       "BR", "America/Manaus":        "BR",
    "America/Bogota":          "CO", "America/Lima":          "PE",
    "America/Santiago":        "CL", "America/Buenos_Aires":  "AR",
    "America/Caracas":         "VE",
    # Europe
    "Europe/London":           "GB", "Europe/Dublin":         "IE",
    "Europe/Paris":            "FR", "Europe/Berlin":         "DE",
    "Europe/Amsterdam":        "NL", "Europe/Brussels":       "BE",
    "Europe/Madrid":           "ES", "Europe/Rome":           "IT",
    "Europe/Warsaw":           "PL", "Europe/Prague":         "CZ",
    "Europe/Budapest":         "HU", "Europe/Bucharest":      "RO",
    "Europe/Sofia":            "BG", "Europe/Athens":         "GR",
    "Europe/Helsinki":         "FI", "Europe/Stockholm":      "SE",
    "Europe/Oslo":             "NO", "Europe/Copenhagen":     "DK",
    "Europe/Zurich":           "CH", "Europe/Vienna":         "AT",
    "Europe/Lisbon":           "PT", "Europe/Kiev":           "UA",
    "Europe/Moscow":           "RU", "Europe/Istanbul":       "TR",
    # Asia
    "Asia/Ho_Chi_Minh":        "VN", "Asia/Saigon":           "VN",
    "Asia/Hanoi":              "VN", "Asia/Bangkok":          "TH",
    "Asia/Jakarta":            "ID", "Asia/Makassar":         "ID",
    "Asia/Jayapura":           "ID", "Asia/Singapore":        "SG",
    "Asia/Kuala_Lumpur":       "MY", "Asia/Manila":           "PH",
    "Asia/Tokyo":              "JP", "Asia/Seoul":            "KR",
    "Asia/Shanghai":           "CN", "Asia/Hong_Kong":        "HK",
    "Asia/Taipei":             "TW", "Asia/Macau":            "MO",
    "Asia/Kolkata":            "IN", "Asia/Calcutta":         "IN",
    "Asia/Mumbai":             "IN", "Asia/Delhi":            "IN",
    "Asia/Dhaka":              "BD", "Asia/Karachi":          "PK",
    "Asia/Colombo":            "LK", "Asia/Kathmandu":        "NP",
    "Asia/Almaty":             "KZ", "Asia/Tashkent":         "UZ",
    "Asia/Riyadh":             "SA", "Asia/Dubai":            "AE",
    "Asia/Kuwait":             "KW", "Asia/Qatar":            "QA",
    "Asia/Bahrain":            "BH", "Asia/Tehran":           "IR",
    "Asia/Jerusalem":          "IL", "Asia/Beirut":           "LB",
    "Asia/Amman":              "JO", "Asia/Baghdad":          "IQ",
    "Asia/Yerevan":            "AM", "Asia/Tbilisi":          "GE",
    "Asia/Baku":               "AZ",
    # Oceania
    "Australia/Sydney":        "AU", "Australia/Melbourne":   "AU",
    "Australia/Brisbane":      "AU", "Australia/Adelaide":    "AU",
    "Australia/Perth":         "AU", "Australia/Darwin":      "AU",
    "Pacific/Auckland":        "NZ", "Pacific/Fiji":          "FJ",
    # Africa
    "Africa/Cairo":            "EG", "Africa/Lagos":          "NG",
    "Africa/Johannesburg":     "ZA", "Africa/Nairobi":        "KE",
    "Africa/Casablanca":       "MA", "Africa/Tunis":          "TN",
    "Africa/Algiers":          "DZ",
}

# ── Locale → ISO-3166-1 alpha-2 country mapping ──────────────────────────────
# Maps browser navigator.language values to expected countries.
LOCALE_COUNTRY: dict[str, str] = {
    "en-US": "US", "en-us": "US",
    "en-GB": "GB", "en-gb": "GB",
    "en-AU": "AU", "en-au": "AU",
    "en-CA": "CA", "en-ca": "CA",
    "en-SG": "SG", "en-sg": "SG",
    "en-PH": "PH", "en-ph": "PH",
    "en-IN": "IN", "en-in": "IN",
    "vi-VN": "VN", "vi-vn": "VN", "vi":    "VN",
    "th-TH": "TH", "th-th": "TH", "th":    "TH",
    "id-ID": "ID", "id-id": "ID", "id":    "ID",
    "ms-MY": "MY", "ms-my": "MY", "ms":    "MY",
    "fil-PH": "PH", "tl-PH": "PH",
    "ja-JP": "JP", "ja-jp": "JP", "ja":    "JP",
    "ko-KR": "KR", "ko-kr": "KR", "ko":    "KR",
    "zh-CN": "CN", "zh-cn": "CN", "zh-Hans": "CN",
    "zh-TW": "TW", "zh-tw": "TW", "zh-Hant": "TW",
    "zh-HK": "HK", "zh-hk": "HK",
    "de-DE": "DE", "de-de": "DE", "de":    "DE",
    "fr-FR": "FR", "fr-fr": "FR", "fr":    "FR",
    "es-ES": "ES", "es-es": "ES",
    "es-MX": "MX", "es-mx": "MX",
    "pt-BR": "BR", "pt-br": "BR",
    "pt-PT": "PT", "pt-pt": "PT",
    "ru-RU": "RU", "ru-ru": "RU", "ru":    "RU",
    "uk-UA": "UA", "uk-ua": "UA",
    "pl-PL": "PL", "pl-pl": "PL", "pl":    "PL",
    "nl-NL": "NL", "nl-nl": "NL", "nl":    "NL",
    "it-IT": "IT", "it-it": "IT", "it":    "IT",
    "tr-TR": "TR", "tr-tr": "TR", "tr":    "TR",
    "ar-SA": "SA", "ar-sa": "SA",
    "ar-AE": "AE", "ar-ae": "AE",
}

# Countries where English locale is common even without US/GB proxy
# (e.g. Singapore, Philippines, India — bilingual populations)
ENGLISH_TOLERANT_COUNTRIES = {"SG", "PH", "IN", "AU", "CA", "NZ", "ZA", "MY", "NG"}


def timezone_to_country(timezone: str) -> str | None:
    """Return ISO country code for a timezone string, or None if unknown."""
    return TIMEZONE_COUNTRY.get(timezone) or TIMEZONE_COUNTRY.get(timezone.replace(" ", "_"))


def locale_to_country(locale: str) -> str | None:
    """Return ISO country code for a locale string, or None if unknown."""
    result = LOCALE_COUNTRY.get(locale)
    if result:
        return result
    # Try base language code only (e.g. "en" from "en-GB")
    base = locale.split("-")[0].lower()
    return LOCALE_COUNTRY.get(base)


def check_geo_consistency(
    timezone: str,
    locale: str,
    proxy_country: str | None,
) -> list[str]:
    """Check whether account timezone, locale, and proxy country are geographically consistent.

    Returns a list of issue strings (empty = consistent).
    Callers should log warnings and optionally add risk points for each issue.

    Rules:
      1. timezone_country should match locale_country (or be English in tolerant countries)
      2. If proxy_country known: timezone_country should match proxy_country
      3. If proxy_country known: locale_country should match proxy_country
    """
    issues: list[str] = []

    tz_country   = timezone_to_country(timezone)
    loc_country  = locale_to_country(locale)

    # Rule 1: timezone vs locale consistency
    if tz_country and loc_country:
        if tz_country != loc_country:
            # Allow English locale in countries where it's common
            is_english_locale = locale.lower().startswith("en")
            if is_english_locale and loc_country in ("US", "GB") and tz_country in ENGLISH_TOLERANT_COUNTRIES:
                pass  # Acceptable: e.g. SG with en-US locale
            elif is_english_locale and tz_country in ENGLISH_TOLERANT_COUNTRIES:
                pass  # Acceptable: e.g. PH with en-US locale
            else:
                issues.append(
                    f"TIMEZONE_LOCALE_MISMATCH: timezone={timezone!r} ({tz_country}) "
                    f"vs locale={locale!r} ({loc_country})"
                )

    # Rules 2 & 3: proxy country consistency
    if proxy_country:
        proxy_country_upper = proxy_country.upper()

        if tz_country and tz_country != proxy_country_upper:
            issues.append(
                f"TIMEZONE_PROXY_MISMATCH: timezone={timezone!r} ({tz_country}) "
                f"vs proxy_country={proxy_country_upper}"
            )

        if loc_country and loc_country != proxy_country_upper:
            is_english = locale.lower().startswith("en")
            if is_english and proxy_country_upper in ENGLISH_TOLERANT_COUNTRIES:
                pass  # Acceptable
            elif is_english and proxy_country_upper in ("US", "GB", "AU", "CA"):
                pass  # en-US / en-GB with English-speaking proxy is fine
            else:
                issues.append(
                    f"LOCALE_PROXY_MISMATCH: locale={locale!r} ({loc_country}) "
                    f"vs proxy_country={proxy_country_upper}"
                )

    return issues


def account_age_days(created_at_str: str | None) -> int | None:
    """Compute account age in days from created_at timestamp string."""
    if not created_at_str:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(created_at_str))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None
