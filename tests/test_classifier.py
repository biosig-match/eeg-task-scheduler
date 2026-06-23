from eeg_task_scheduler.classifier import ActivityWindow, classify_state


def test_classifies_overload_stop() -> None:
    label, severity, _ = classify_state(
        engagement=0.8,
        workload=1.4,
        activity=ActivityWindow(0, 0.0, 0, 0, 60.0),
    )
    assert label == "過負荷停止"
    assert severity == "warning"


def test_classifies_flow() -> None:
    label, severity, _ = classify_state(
        engagement=0.8,
        workload=0.8,
        activity=ActivityWindow(20, 1200.0, 3, 2, 2.0),
    )
    assert label == "フロー"
    assert severity == "good"

