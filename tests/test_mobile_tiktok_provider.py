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


def test_mobile_media_diff_detects_newest_new_file():
    from core.mobile_tiktok_provider import MobileMediaFile, _find_new_mobile_media_file

    before = {
        "/sdcard/DCIM/old.mp4": MobileMediaFile("/sdcard/DCIM/old.mp4", size=100, modified_at=10),
    }
    after = {
        **before,
        "/sdcard/Movies/newer.mp4": MobileMediaFile("/sdcard/Movies/newer.mp4", size=200, modified_at=20),
        "/sdcard/Download/newest.mp4": MobileMediaFile("/sdcard/Download/newest.mp4", size=300, modified_at=30),
    }

    assert _find_new_mobile_media_file(before, after) == after["/sdcard/Download/newest.mp4"]


def test_save_button_ui_node_matching_supports_vietnamese_label():
    from core.mobile_tiktok_provider import _find_actionable_ui_node

    xml = (
        '<hierarchy>'
        '<node text="Tải xuống" bounds="[10,20][110,120]" />'
        '<node content-desc="Share" bounds="[200,20][260,80]" />'
        '</hierarchy>'
    )

    node = _find_actionable_ui_node(xml, role="save")

    assert node is not None
    assert node["text"] == "Tải xuống"
