from eeg_task_scheduler.eeg.features import EegFeatureMonitor, synthetic_sample


def test_feature_monitor_computes_band_metrics() -> None:
    monitor = EegFeatureMonitor(window_seconds=1.0, step_seconds=0.5, channel_count=8)
    for index in range(260):
        monitor.add(synthetic_sample(index))
    status = monitor.status()
    assert status["ready"]
    data = status["data"]
    assert data["theta"] > 0
    assert data["engagement"] >= 0
    assert "approach_avoidance" in data
    assert not data["approach_avoidance_available"]
    assert data["electrode_names"][:3] == ["C3", "Cz", "C4"]


def test_feature_monitor_computes_approach_avoidance_when_f3_f4_are_available() -> None:
    monitor = EegFeatureMonitor(
        window_seconds=1.0,
        step_seconds=0.5,
        channel_count=8,
        electrode_names=("F3", "F4", "C3", "Cz", "C4", "", "", ""),
    )
    for index in range(260):
        monitor.add(synthetic_sample(index))

    data = monitor.status()["data"]

    assert data["approach_avoidance_available"]
    assert isinstance(data["approach_avoidance"], float)
