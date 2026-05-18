from __future__ import annotations


def test_adb_devices_has_available_device():
    from core.mobile_tiktok_provider import _adb_devices_has_available_device

    stdout = "List of devices attached\nemulator-5554\tdevice\nother\toffline\n"

    assert _adb_devices_has_available_device(stdout)
    assert _adb_devices_has_available_device(stdout, "emulator-5554")
    assert not _adb_devices_has_available_device(stdout, "missing-device")


def test_adb_devices_rejects_empty_or_unauthorized():
    from core.mobile_tiktok_provider import _adb_devices_has_available_device

    assert not _adb_devices_has_available_device("List of devices attached\n")
    assert not _adb_devices_has_available_device("List of devices attached\nemulator-5554\tunauthorized\n")


def test_ui_xml_video_link_extraction_helpers():
    from core.mobile_tiktok_provider import _canonical_tiktok_url, _ui_text_values

    xml = (
        '<hierarchy><node text="Shop demo" />'
        '<node content-desc="https://www.tiktok.com/@shop/video/123?lang=en" /></hierarchy>'
    )

    values = _ui_text_values(xml)

    assert "Shop demo" in values
    assert _canonical_tiktok_url("https://www.tiktok.com/@shop/video/123?lang=en#comments") == (
        "https://www.tiktok.com/@shop/video/123"
    )
