from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActivityWindow:
    key_count: int
    mouse_distance: float
    click_count: int
    scroll_count: int
    idle_seconds: float

    @property
    def activity_score(self) -> float:
        return self.key_count + self.click_count * 4 + self.scroll_count * 2 + self.mouse_distance / 250.0


def classify_state(engagement: float, workload: float, activity: ActivityWindow) -> tuple[str, str, str]:
    high_activity = activity.activity_score >= 12 and activity.idle_seconds < 20
    low_activity = activity.activity_score < 4 or activity.idle_seconds >= 45
    high_workload = workload >= 1.15
    low_engagement = engagement < 0.35

    if high_workload and low_activity:
        return (
            "過負荷停止",
            "warning",
            "認知負荷が高いまま操作量が落ちており、内容の難しさによる停滞の可能性があります。",
        )
    if low_engagement and low_activity:
        return (
            "逸脱停止",
            "muted",
            "関与指標と操作量がともに低く、作業から離れている可能性があります。",
        )
    if high_activity and not high_workload:
        return (
            "フロー",
            "good",
            "操作量があり、負荷が過度ではないため順調な集中区間と判定しました。",
        )
    return (
        "通常",
        "neutral",
        "操作量と認知負荷が大きく崩れていない通常区間です。",
    )

