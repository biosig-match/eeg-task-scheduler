from eeg_task_scheduler.config import parse_electrode_names


def test_parse_electrode_names_defaults_to_current_three_channel_layout() -> None:
    assert parse_electrode_names(None) == ("C3", "Cz", "C4", "", "", "", "", "")


def test_parse_electrode_names_pads_to_eight_channels() -> None:
    assert parse_electrode_names("F3,F4,C3,Cz,C4") == ("F3", "F4", "C3", "Cz", "C4", "", "", "")
