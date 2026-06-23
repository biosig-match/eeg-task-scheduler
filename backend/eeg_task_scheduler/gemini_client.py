from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


PRIVATE_TERMS = ("password", "パスワード", "card number", "クレジット", "決済", "dm", "private", "秘密")


@dataclass(frozen=True)
class ScreenAnalysis:
    description: str
    ocr_text: str
    privacy_state: str


class GeminiClient:
    def __init__(self, api_key: str | None, model: str, embedding_model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.embedding_model = embedding_model
        self.available = bool(api_key)
        self._client: Any | None = None
        if self.available:
            try:
                from google import genai

                self._client = genai.Client(api_key=api_key)
            except Exception:
                self.available = False

    def analyze_screen(self, image_path: Path, source_name: str, todo: str) -> ScreenAnalysis:
        if self._looks_private(source_name):
            return ScreenAnalysis(
                description="秘匿対象の可能性があるため、画像は外部モデルへ送信しませんでした。",
                ocr_text="",
                privacy_state="blocked",
            )
        if not self.available or self._client is None:
            return ScreenAnalysis(
                description=f"{source_name} の画面観測をローカルに記録しました。Gemini未接続のため詳細説明は未生成です。",
                ocr_text="",
                privacy_state="local_only",
            )
        try:
            image_bytes = image_path.read_bytes()
            prompt = (
                "You are summarizing a knowledge worker's screen for a private Pomodoro report. "
                "Do not reveal secrets. Return concise Japanese JSON-like text with fields "
                "description and ocr_text. Current todo: "
                f"{todo}"
            )
            response = self._client.models.generate_content(
                model=self.model,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {"text": prompt},
                            {
                                "inline_data": {
                                    "mime_type": "image/png",
                                    "data": base64.b64encode(image_bytes).decode("ascii"),
                                }
                            },
                        ],
                    }
                ],
            )
            text = getattr(response, "text", "") or ""
            return ScreenAnalysis(description=text.strip()[:4000], ocr_text="", privacy_state="sent")
        except Exception as error:
            return ScreenAnalysis(
                description=f"Gemini解析に失敗しました: {error}",
                ocr_text="",
                privacy_state="error",
            )

    def summarize_report(self, todo: str, timeline: list[dict[str, Any]], memories: list[str]) -> tuple[str, list[str]]:
        fallback = self._fallback_report(todo, timeline, memories)
        if not self.available or self._client is None:
            return fallback
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    "次の25分ポモドーロ記録を日本語で短く振り返り、次回Todo案を3つまで出してください。"
                                    "ユーザ承認なしに外部更新しない前提です。\n"
                                    f"Todo: {todo}\nTimeline: {timeline}\nRelevant memories: {memories}"
                                )
                            }
                        ],
                    }
                ],
            )
            text = (getattr(response, "text", "") or "").strip()
            if not text:
                return fallback
            suggestions = []
            for line in text.splitlines():
                stripped = line.strip()
                if not re.match(r"^(-|・|\d+[.)]|[０-９]+[.)．])", stripped):
                    continue
                suggestion = re.sub(r"^(-|・|\d+[.)]|[０-９]+[.)．])\s*", "", stripped).strip()
                suggestion = suggestion.strip("* ")
                if suggestion:
                    suggestions.append(suggestion)
            return text, suggestions[:3] or fallback[1]
        except Exception:
            return fallback

    def summarize_phases(self, task: str, episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        fallback = self._fallback_phases(episodes)
        if not self.available or self._client is None:
            return fallback
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    "30秒単位の作業エピソードを、連続した作業フェーズにまとめてください。"
                                    "フェーズ粒度は数分から十数分程度で、例: 調査, 実装, デバッグ, 文書化, 停滞。"
                                    "JSON配列のみを返してください。各要素は "
                                    "{phase_type,title,summary,episode_ids,completed,evidence,next_task}。"
                                    "completedは、そのフェーズで小タスクが完了したと強く言える時だけtrue。\n"
                                    f"Task: {task}\nEpisodes: {episodes}"
                                )
                            }
                        ],
                    }
                ],
            )
            text = (getattr(response, "text", "") or "").strip()
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [phase for phase in parsed if isinstance(phase, dict)] or fallback
            return fallback
        except Exception:
            return fallback

    def summarize_episode(
        self,
        todo: str,
        active_window: str,
        screen_description: str,
        eeg_features: dict[str, Any],
        activity: dict[str, Any],
    ) -> str:
        fallback = (
            f"Todo「{todo}」に対して、{active_window} での作業を観測しました。"
            f"画面文脈: {screen_description[:240]} "
            f"EEG engagement={eeg_features.get('engagement', 'n/a')}, "
            f"workload={eeg_features.get('workload', 'n/a')}。"
        )
        if not self.available or self._client is None:
            return fallback
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    "30秒の作業エピソードを、現在Todoとの関係が分かるように日本語で2文以内に要約してください。"
                                    "秘密情報は書かず、作業の種類・進捗・停滞要因だけを述べてください。\n"
                                    f"Todo: {todo}\n"
                                    f"Active window: {active_window}\n"
                                    f"Screen: {screen_description}\n"
                                    f"EEG: {eeg_features}\n"
                                    f"Activity: {activity}"
                                )
                            }
                        ],
                    }
                ],
            )
            text = (getattr(response, "text", "") or "").strip()
            return text[:1200] if text else fallback
        except Exception:
            return fallback

    def embed(self, text: str) -> list[float] | None:
        if not self.available or self._client is None:
            return None
        try:
            response = self._client.models.embed_content(model=self.embedding_model, contents=text)
            embedding = response.embeddings[0].values
            return [float(value) for value in embedding]
        except Exception:
            return None

    def _fallback_report(self, todo: str, timeline: list[dict[str, Any]], memories: list[str]) -> tuple[str, list[str]]:
        event_count = len(timeline)
        warnings = [item for item in timeline if item.get("severity") == "warning"]
        summary = (
            f"今回のTodo「{todo}」では、{event_count}件の状態区間を記録しました。"
            f"過負荷停止は{len(warnings)}件でした。"
            "詳細な画面説明はGemini接続後により具体化できます。"
        )
        if memories:
            summary += f" 過去の類似記録を{len(memories)}件参照しました。"
        suggestions = [
            "次回は最初の一手を確認可能な小タスクに分ける",
            "高負荷区間の直前に見ていた資料やエラーを先に整理する",
            "25分内で完了条件を1つだけ置く",
        ]
        return summary, suggestions

    def _fallback_phases(self, episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not episodes:
            return []
        first = episodes[0]
        last = episodes[-1]
        summaries = [str(episode.get("work_summary", "")) for episode in episodes]
        warning_count = sum(1 for episode in episodes if episode.get("severity") == "warning")
        completed = warning_count == 0 and len(episodes) >= 2
        return [
            {
                "phase_type": "作業",
                "title": "観測された作業フェーズ",
                "summary": " ".join(summaries)[:900],
                "episode_ids": [episode.get("id") for episode in episodes],
                "completed": completed,
                "evidence": "過負荷停止が少なく、連続した作業エピソードが記録されました。" if completed else "停滞または短すぎるため完了とは判定しません。",
                "next_task": "次に確認可能な一手を進める",
                "started_at": first.get("started_at"),
                "ended_at": last.get("ended_at"),
            }
        ]

    def _looks_private(self, value: str) -> bool:
        lower = value.lower()
        return any(term in lower for term in PRIVATE_TERMS)
