from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class NotionTask:
    id: str
    title: str
    status: str
    project_ids: tuple[str, ...]
    project_names: tuple[str, ...]
    due: str | None
    url: str

    def as_todo(self) -> str:
        project = f"[{', '.join(self.project_names)}] " if self.project_names else ""
        due = f" (due: {self.due})" if self.due else ""
        status = f" / {self.status}" if self.status else ""
        return f"{project}{self.title}{due}{status}"


class NotionClient:
    def __init__(
        self,
        api_key: str | None,
        tasks_data_source_id: str | None,
        projects_data_source_id: str | None = None,
        notion_version: str = "2025-09-03",
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.tasks_data_source_id = _compact_id(tasks_data_source_id)
        self.projects_data_source_id = _compact_id(projects_data_source_id)
        self.notion_version = notion_version
        timeout = httpx.Timeout(8.0, connect=4.0)
        self.client = client or httpx.Client(timeout=timeout)
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.tasks_data_source_id)

    def status(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "tasks_data_source_id": bool(self.tasks_data_source_id),
            "projects_data_source_id": bool(self.projects_data_source_id),
            "notion_version": self.notion_version,
            "last_error": self.last_error,
        }

    def fetch_open_tasks(self, limit: int = 20) -> list[NotionTask]:
        if not self.configured:
            return []
        try:
            pages = self._query_data_source(self.tasks_data_source_id, page_size=min(limit, 100))
            tasks = [self._parse_task(page, resolve_project_names=False) for page in pages]
            open_tasks = [task for task in tasks if not _is_done(task.status)]
            self.last_error = ""
            return open_tasks[:limit]
        except httpx.HTTPError as error:
            self.last_error = _http_error_message(error)
            raise

    def initial_todo(self) -> NotionTask | None:
        tasks = self.fetch_open_tasks(limit=10)
        if not tasks:
            return None
        dated = [task for task in tasks if task.due]
        if dated:
            return sorted(dated, key=lambda task: task.due or "")[0]
        return tasks[0]

    def create_project(self, name: str, summary: str = "", status: str = "Planning") -> NotionTask | None:
        if not self.api_key or not self.projects_data_source_id:
            return None
        payload = {
            "parent": {"type": "data_source_id", "data_source_id": self.projects_data_source_id},
            "properties": {
                "Project name": {"title": [{"text": {"content": name}}]},
                "Status": {"status": {"name": status}},
            },
        }
        if summary:
            payload["properties"]["Summary"] = {"rich_text": [{"text": {"content": summary[:1900]}}]}
        page = self._create_page(payload)
        return self._parse_task(page, resolve_project_names=True) if page else None

    def create_task(
        self,
        title: str,
        project_ids: tuple[str, ...] = (),
        parent_task_id: str | None = None,
        due: str | None = None,
        status: str = "Not Started",
        priority: str | None = None,
        note: str | None = None,
    ) -> NotionTask | None:
        if not self.configured:
            return None
        properties: dict[str, Any] = {
            "Task name": {"title": [{"text": {"content": title[:1900]}}]},
            "Status": {"status": {"name": status}},
        }
        if due:
            properties["Due"] = {"date": {"start": due}}
        if priority:
            properties["Priority"] = {"select": {"name": priority}}
        if project_ids:
            properties["Project"] = {"relation": [{"id": project_id} for project_id in project_ids]}
        if parent_task_id:
            properties["Parent-task"] = {"relation": [{"id": parent_task_id}]}
        payload: dict[str, Any] = {
            "parent": {"type": "data_source_id", "data_source_id": self.tasks_data_source_id},
            "properties": properties,
        }
        if note:
            payload["children"] = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": note[:1900]}}]},
                }
            ]
        page = self._create_page(payload)
        return self._parse_task(page, resolve_project_names=True) if page else None

    def update_task_status(self, page_id: str, status: str) -> bool:
        return self._update_page(page_id, {"properties": {"Status": {"status": {"name": status}}}})

    def add_comment(self, page_id: str, text: str) -> bool:
        if not self.api_key:
            return False
        response = self.client.post(
            "https://api.notion.com/v1/comments",
            headers=self._headers(),
            json={
                "parent": {"page_id": page_id},
                "rich_text": [{"type": "text", "text": {"content": text[:1900]}}],
            },
        )
        return response.status_code < 400

    def _query_data_source(self, data_source_id: str | None, page_size: int = 20) -> list[dict[str, Any]]:
        if not data_source_id:
            return []
        data_source_id = self._resolve_data_source_id(data_source_id)
        payload = {"page_size": page_size}
        response = self.client.post(
            f"https://api.notion.com/v1/data_sources/{data_source_id}/query",
            headers=self._headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return list(data.get("results", []))

    def _resolve_data_source_id(self, source_or_database_id: str) -> str:
        source_or_database_id = _compact_id(source_or_database_id) or source_or_database_id
        response = self.client.get(
            f"https://api.notion.com/v1/data_sources/{source_or_database_id}",
            headers=self._headers(),
        )
        if response.status_code < 400:
            return source_or_database_id
        if response.status_code != 404:
            response.raise_for_status()

        database_response = self.client.get(
            f"https://api.notion.com/v1/databases/{source_or_database_id}",
            headers=self._headers(),
        )
        database_response.raise_for_status()
        data_sources = database_response.json().get("data_sources", [])
        data_source_id = next((item.get("id") for item in data_sources if item.get("id")), None)
        if not data_source_id:
            raise RuntimeError("No data_sources were found for the configured Notion database")
        compact_id = _compact_id(str(data_source_id)) or str(data_source_id)
        if source_or_database_id == self.tasks_data_source_id:
            self.tasks_data_source_id = compact_id
        if source_or_database_id == self.projects_data_source_id:
            self.projects_data_source_id = compact_id
        return compact_id

    def _create_page(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        response = self.client.post("https://api.notion.com/v1/pages", headers=self._headers(), json=payload)
        response.raise_for_status()
        return response.json()

    def _update_page(self, page_id: str, payload: dict[str, Any]) -> bool:
        response = self.client.patch(f"https://api.notion.com/v1/pages/{page_id}", headers=self._headers(), json=payload)
        response.raise_for_status()
        return True

    def _retrieve_page(self, page_id: str) -> dict[str, Any] | None:
        response = self.client.get(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=self._headers(),
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def _parse_task(self, page: dict[str, Any], resolve_project_names: bool = True) -> NotionTask:
        properties = page.get("properties", {})
        title = ""
        status = ""
        project_ids: list[str] = []
        project_names: list[str] = []
        due: str | None = None

        for value in properties.values():
            kind = value.get("type")
            if kind == "title" and not title:
                title = _rich_text_plain(value.get("title", []))
            elif kind == "status" and not status:
                status = (value.get("status") or {}).get("name", "")
            elif kind == "select" and not status:
                status = (value.get("select") or {}).get("name", "")
            elif kind == "relation":
                for relation in value.get("relation", []):
                    related_id = relation.get("id")
                    if related_id:
                        project_ids.append(related_id)
            elif kind == "date" and due is None:
                date_value = value.get("date") or {}
                due = date_value.get("start")

        if resolve_project_names:
            for project_id in project_ids:
                project = self._retrieve_page(project_id)
                if project:
                    project_name = _page_title(project)
                    if project_name:
                        project_names.append(project_name)

        return NotionTask(
            id=page.get("id", ""),
            title=title or "Untitled task",
            status=status,
            project_ids=tuple(project_ids),
            project_names=tuple(project_names),
            due=due,
            url=page.get("url", ""),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": self.notion_version,
            "Content-Type": "application/json",
        }


def _page_title(page: dict[str, Any]) -> str:
    for value in page.get("properties", {}).values():
        if value.get("type") == "title":
            return _rich_text_plain(value.get("title", []))
    return ""


def _rich_text_plain(parts: list[dict[str, Any]]) -> str:
    return "".join(str(part.get("plain_text", "")) for part in parts).strip()


def _is_done(status: str) -> bool:
    normalized = status.strip().lower()
    return normalized in {"done", "complete", "completed", "完了", "終了", "達成"}


def _http_error_message(error: httpx.HTTPError) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        response = error.response
        try:
            body = response.json()
            message = body.get("message") or body.get("detail") or response.text
        except ValueError:
            message = response.text
        return f"Notion API {response.status_code}: {message}"
    return str(error)


def _compact_id(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().replace("-", "")
