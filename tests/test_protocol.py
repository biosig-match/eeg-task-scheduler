from eeg_task_scheduler.eeg.protocol import PacketParser, EegSample


def test_parser_reads_data_chunk() -> None:
    packet = bytearray([0x66, 0x2A, 0x00, 0x01])
    packet.extend((1).to_bytes(2, "little", signed=True) * 8)
    packet.extend(bytes([7, 0, 0, 0]))
    messages = PacketParser().feed(packet)
    assert len(messages) == 1
    assert isinstance(messages[0], EegSample)
    assert messages[0].sample_index == 42
    assert messages[0].trigger == 7

