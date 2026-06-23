from __future__ import annotations

from dataclasses import dataclass

from eeg_task_scheduler.eeg.ble import SERVICE_UUID, ScannedDevice, _format_scan_detail, _select_ads1299_device


@dataclass
class FakeDevice:
    name: str | None
    address: str


@dataclass
class FakeAdvertisement:
    local_name: str | None = None
    service_uuids: list[str] | None = None
    rssi: int | None = None


def scanned(
    name: str | None,
    address: str,
    local_name: str | None = None,
    service_uuids: list[str] | None = None,
) -> ScannedDevice:
    return ScannedDevice(
        device=FakeDevice(name=name, address=address),
        advertisement=FakeAdvertisement(local_name=local_name, service_uuids=service_uuids),
    )


def test_selects_device_by_advertisement_local_name() -> None:
    target = scanned(None, "AA:BB:CC:DD:EE:01", local_name="ADS1299_EEG_NUS")
    other = scanned("Keyboard", "AA:BB:CC:DD:EE:02")

    assert _select_ads1299_device([other, target], "ADS1299_EEG_NUS") is target


def test_selects_unique_nus_service_when_name_is_missing() -> None:
    target = scanned(None, "AA:BB:CC:DD:EE:01", service_uuids=[SERVICE_UUID.lower()])
    other = scanned("Mouse", "AA:BB:CC:DD:EE:02")

    assert _select_ads1299_device([other, target], "ADS1299_EEG_NUS") is target


def test_address_override_has_priority() -> None:
    named = scanned("ADS1299_EEG_NUS", "AA:BB:CC:DD:EE:01")
    addressed = scanned("Unknown", "AA-BB-CC-DD-EE-02")

    assert _select_ads1299_device(
        [named, addressed],
        "ADS1299_EEG_NUS",
        "aa:bb:cc:dd:ee:02",
    ) is addressed


def test_scan_detail_includes_seen_devices() -> None:
    detail = _format_scan_detail(
        [scanned(None, "AA:BB:CC:DD:EE:01", local_name="ADS1299_EEG_NUS", service_uuids=[SERVICE_UUID])]
    )

    assert "ADS1299_EEG_NUS" in detail
    assert "AA:BB:CC:DD:EE:01" in detail
    assert "NUS" in detail
