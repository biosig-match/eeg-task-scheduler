from __future__ import annotations

from dataclasses import dataclass
import struct

PKT_TYPE_DATA_CHUNK = 0x66
PKT_TYPE_DEVICE_CFG = 0xDD
DEVICE_CONFIG_PACKET_SIZE = 88
SAMPLE_DATA_SIZE_BYTES = 20
MAX_SAMPLES_PER_CHUNK = 25


@dataclass(frozen=True)
class DeviceConfig:
    num_channels: int
    electrode_names: tuple[str, ...]


@dataclass(frozen=True)
class EegSample:
    sample_index: int
    signals: tuple[int, ...]
    trigger: int


class PacketParser:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, data: bytes | bytearray) -> list[DeviceConfig | EegSample]:
        self.buffer.extend(data)
        messages: list[DeviceConfig | EegSample] = []
        while self.buffer:
            if self.buffer[0] not in (PKT_TYPE_DEVICE_CFG, PKT_TYPE_DATA_CHUNK):
                positions = [
                    position
                    for marker in (PKT_TYPE_DEVICE_CFG, PKT_TYPE_DATA_CHUNK)
                    if (position := self.buffer.find(bytes([marker]))) >= 0
                ]
                if not positions:
                    self.buffer.clear()
                    break
                del self.buffer[: min(positions)]

            packet_type = self.buffer[0]
            if packet_type == PKT_TYPE_DEVICE_CFG:
                expected_length = DEVICE_CONFIG_PACKET_SIZE
            else:
                if len(self.buffer) < 4:
                    break
                sample_count = self.buffer[3]
                if not 1 <= sample_count <= MAX_SAMPLES_PER_CHUNK:
                    del self.buffer[0]
                    continue
                expected_length = 4 + sample_count * SAMPLE_DATA_SIZE_BYTES

            if len(self.buffer) < expected_length:
                break
            packet = bytes(self.buffer[:expected_length])
            del self.buffer[:expected_length]
            if packet_type == PKT_TYPE_DEVICE_CFG:
                messages.append(_parse_config(packet))
            else:
                messages.extend(_parse_samples(packet))
        return messages


def _parse_config(data: bytes) -> DeviceConfig:
    _, num_channels = struct.unpack_from("<BB", data)
    names = []
    for index in range(8):
        name_bytes, _, _ = struct.unpack_from("<8sBB", data, 8 + index * 10)
        names.append(name_bytes.partition(b"\0")[0].decode("utf-8", "ignore"))
    return DeviceConfig(num_channels=num_channels, electrode_names=tuple(names))


def _parse_samples(data: bytes) -> list[EegSample]:
    _, start_index, sample_count = struct.unpack_from("<BHB", data)
    samples = []
    for index in range(sample_count):
        values = struct.unpack_from("<8hB3x", data, 4 + index * SAMPLE_DATA_SIZE_BYTES)
        samples.append(
            EegSample(
                sample_index=(start_index + index) & 0xFFFF,
                signals=tuple(values[:8]),
                trigger=values[8],
            )
        )
    return samples

