from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
import time
from typing import Any

from bleak import BleakClient, BleakScanner

from .features import EegFeatureMonitor
from .protocol import DeviceConfig, EegSample, PacketParser
from ..time_utils import now_iso

SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
CHARACTERISTIC_UUID_TX = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
CHARACTERISTIC_UUID_RX = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
CMD_START_STREAMING = b"\xAA"
CMD_STOP_STREAMING = b"\x5B"
SCAN_TIMEOUTS_SECONDS = (6.0, 10.0, 14.0)
MAX_SCAN_DETAIL_DEVICES = 8

MessageHandler = Callable[[DeviceConfig | EegSample], Awaitable[None] | None]
StatusHandler = Callable[[str, str], None]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScannedDevice:
    device: Any
    advertisement: Any | None


class Ads1299BleClient:
    def __init__(
        self,
        device_name: str,
        on_message: MessageHandler,
        on_status: StatusHandler | None = None,
        feature_monitor: EegFeatureMonitor | None = None,
        device_address: str | None = None,
    ) -> None:
        self.device_name = device_name
        self.device_address = device_address
        self.on_message = on_message
        self.on_status = on_status
        self.client: BleakClient | None = None
        self.parser = PacketParser()
        self.features = feature_monitor or EegFeatureMonitor()
        self.state = "disconnected"
        self.detail = "未接続"
        self.sample_count = 0
        self.notification_count = 0
        self.received_bytes = 0
        self.last_sample_index: int | None = None
        self.first_received_ns: int | None = None
        self.last_received_ns: int | None = None
        self.dropped_sample_count = 0
        self.duplicate_sample_count = 0
        self.out_of_order_sample_count = 0
        self.discontinuity_count = 0
        self.last_received_at: str | None = None
        self.last_scan_detail = ""
        self.history: deque[dict[str, str]] = deque(maxlen=30)
        self._append_history("disconnected", "未接続")

    @property
    def streaming(self) -> bool:
        return self.state == "streaming" and self.client is not None and self.client.is_connected

    @property
    def receiving(self) -> bool:
        return self.streaming and self.sample_count > 0

    def status(self) -> dict[str, object]:
        elapsed_seconds = (
            (self.last_received_ns - self.first_received_ns) / 1_000_000_000
            if self.first_received_ns is not None and self.last_received_ns is not None
            else 0.0
        )
        return {
            "state": self.state,
            "detail": self.detail,
            "streaming": self.streaming,
            "receiving": self.receiving,
            "device_name": self.device_name,
            "device_address": self.device_address,
            "last_scan_detail": self.last_scan_detail,
            "sample_count": self.sample_count,
            "notification_count": self.notification_count,
            "received_bytes": self.received_bytes,
            "last_sample_index": self.last_sample_index,
            "dropped_sample_count": self.dropped_sample_count,
            "duplicate_sample_count": self.duplicate_sample_count,
            "out_of_order_sample_count": self.out_of_order_sample_count,
            "discontinuity_count": self.discontinuity_count,
            "last_received_at": self.last_received_at,
            "estimated_sample_rate": (
                round((self.sample_count - 1) / elapsed_seconds, 1)
                if elapsed_seconds > 0 and self.sample_count > 1
                else None
            ),
            "history": list(self.history),
        }

    async def connect(self) -> None:
        try:
            device = await self._find_device()
            self._set_status("connecting", f"{device.name} ({device.address}) に接続中")
            self.client = BleakClient(device, timeout=30.0, disconnected_callback=self._disconnected)
            await self.client.connect()
            self._set_status("subscribing", "EEG通知を準備中")
            await self.client.start_notify(CHARACTERISTIC_UUID_TX, self._notification_handler)
            self._set_status("connected", f"{self.device_name} に接続済み")
        except Exception as error:
            self.client = None
            self._set_status("error", str(error))
            raise

    async def _find_device(self) -> Any:
        last_seen: list[ScannedDevice] = []
        target = self.device_address or self.device_name
        self._set_status(
            "scanning",
            f"{target} を検索中 (最大{sum(SCAN_TIMEOUTS_SECONDS):.0f}秒)",
        )
        for attempt, timeout in enumerate(SCAN_TIMEOUTS_SECONDS, start=1):
            devices = await _discover_devices(timeout)
            last_seen = devices
            self.last_scan_detail = _format_scan_detail(devices)
            selected = _select_ads1299_device(devices, self.device_name, self.device_address)
            if selected is not None:
                return selected.device
            await asyncio.sleep(0.5)
        self.last_scan_detail = _format_scan_detail(last_seen)
        raise RuntimeError(
            f"BLE device not found: {self.device_name}. "
            "The expected device name or NUS service UUID was not visible in BLE advertisements."
        )

    async def start(self) -> None:
        if self.streaming:
            return
        if self.client is None or not self.client.is_connected:
            await self.connect()
        assert self.client is not None
        self._set_status("starting", "EEGストリームを開始中")
        self._reset_counters()
        await self.client.write_gatt_char(CHARACTERISTIC_UUID_RX, CMD_START_STREAMING, response=True)
        self._set_status("streaming", "EEG受信準備完了")

    async def stop(self) -> None:
        if self.client is None:
            self._set_status("disconnected", "未接続")
            return
        try:
            self._set_status("disconnecting", "BLEを切断中")
            if self.client.is_connected:
                await self.client.write_gatt_char(CHARACTERISTIC_UUID_RX, CMD_STOP_STREAMING, response=True)
                await self.client.stop_notify(CHARACTERISTIC_UUID_TX)
                await self.client.disconnect()
        finally:
            self.client = None
            self._set_status("disconnected", "未接続")

    def ingest_sample(self, sample: EegSample) -> None:
        self._handle_message(sample)

    def _notification_handler(self, _sender: object, data: bytearray) -> None:
        self.notification_count += 1
        self.received_bytes += len(data)
        for message in self.parser.feed(data):
            self._handle_message(message)

    def _handle_message(self, message: DeviceConfig | EegSample) -> None:
        if isinstance(message, EegSample):
            first_sample = self.sample_count == 0
            received_ns = time.monotonic_ns()
            if self.last_sample_index is not None:
                step = (message.sample_index - self.last_sample_index) & 0xFFFF
                if step == 0:
                    self.duplicate_sample_count += 1
                elif step < 0x8000:
                    if step > 1:
                        self.dropped_sample_count += step - 1
                        self.discontinuity_count += 1
                else:
                    self.out_of_order_sample_count += 1
                    self.discontinuity_count += 1
            self.sample_count += 1
            self.last_sample_index = message.sample_index
            if self.first_received_ns is None:
                self.first_received_ns = received_ns
            self.last_received_ns = received_ns
            self.last_received_at = now_iso()
            self.features.add(message)
            if first_sample and self.state != "streaming":
                self._set_status("streaming", "EEGデータ受信中")
        result = self.on_message(message)
        if asyncio.iscoroutine(result):
            asyncio.create_task(result)

    def _reset_counters(self) -> None:
        self.parser = PacketParser()
        self.features.reset()
        self.sample_count = 0
        self.notification_count = 0
        self.received_bytes = 0
        self.last_sample_index = None
        self.first_received_ns = None
        self.last_received_ns = None
        self.dropped_sample_count = 0
        self.duplicate_sample_count = 0
        self.out_of_order_sample_count = 0
        self.discontinuity_count = 0
        self.last_received_at = None

    def _disconnected(self, _client: BleakClient) -> None:
        self.client = None
        self._set_status("disconnected", "BLE接続が切断されました")

    def _set_status(self, state: str, detail: str) -> None:
        self.state = state
        self.detail = detail
        logger.info("BLE status: %s (%s)", state, detail)
        self._append_history(state, detail)
        if self.on_status:
            self.on_status(state, detail)

    def _append_history(self, state: str, detail: str) -> None:
        self.history.append({"at": now_iso(), "state": state, "detail": detail})


async def _discover_devices(timeout: float) -> list[ScannedDevice]:
    try:
        discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    except TypeError:
        return [ScannedDevice(device=device, advertisement=None) for device in await BleakScanner.discover(timeout=timeout)]
    if isinstance(discovered, dict):
        return [ScannedDevice(device=device, advertisement=advertisement) for device, advertisement in discovered.values()]
    return [ScannedDevice(device=device, advertisement=None) for device in discovered]


def _select_ads1299_device(
    devices: list[ScannedDevice],
    device_name: str,
    device_address: str | None = None,
) -> ScannedDevice | None:
    if device_address:
        normalized_address = _normalize_address(device_address)
        for scanned in devices:
            if _normalize_address(str(getattr(scanned.device, "address", ""))) == normalized_address:
                return scanned

    for scanned in devices:
        if _matches_device_name(scanned, device_name):
            return scanned

    nus_devices = [scanned for scanned in devices if _advertises_service(scanned, SERVICE_UUID)]
    if len(nus_devices) == 1:
        return nus_devices[0]
    return None


def _matches_device_name(scanned: ScannedDevice, device_name: str) -> bool:
    target = _normalize_name(device_name)
    for name in _advertised_names(scanned):
        normalized = _normalize_name(name)
        if normalized == target or normalized.startswith(target) or target in normalized:
            return True
    return False


def _advertised_names(scanned: ScannedDevice) -> tuple[str, ...]:
    names = []
    device_name = getattr(scanned.device, "name", None)
    advertisement_name = getattr(scanned.advertisement, "local_name", None)
    for name in (device_name, advertisement_name):
        if name and str(name) not in names:
            names.append(str(name))
    return tuple(names)


def _advertises_service(scanned: ScannedDevice, service_uuid: str) -> bool:
    service_uuids = getattr(scanned.advertisement, "service_uuids", None) or []
    return service_uuid.lower() in {str(uuid).lower() for uuid in service_uuids}


def _format_scan_detail(devices: list[ScannedDevice]) -> str:
    summaries = []
    for scanned in devices[:MAX_SCAN_DETAIL_DEVICES]:
        names = "/".join(_advertised_names(scanned)) or "(no name)"
        address = getattr(scanned.device, "address", "?")
        rssi = getattr(scanned.advertisement, "rssi", None)
        service_mark = " NUS" if _advertises_service(scanned, SERVICE_UUID) else ""
        rssi_mark = f" {rssi}dBm" if rssi is not None else ""
        summaries.append(f"{names} [{address}{rssi_mark}{service_mark}]")
    if len(devices) > MAX_SCAN_DETAIL_DEVICES:
        summaries.append(f"...+{len(devices) - MAX_SCAN_DETAIL_DEVICES}")
    return "; ".join(summaries)


def _normalize_name(name: str) -> str:
    return "".join(character for character in name.casefold() if character.isalnum())


def _normalize_address(address: str) -> str:
    return address.casefold().replace("-", ":")
