from eeg_task_scheduler.notion_client import NotionClient
import httpx


def test_parse_notion_task_properties() -> None:
    client = NotionClient("secret", "tasks")
    page = {
        "id": "task-id",
        "url": "https://notion.so/task",
        "properties": {
            "Name": {
                "type": "title",
                "title": [{"plain_text": "EEGレポートを書く"}],
            },
            "Status": {
                "type": "status",
                "status": {"name": "In progress"},
            },
            "Due": {
                "type": "date",
                "date": {"start": "2026-06-24"},
            },
        },
    }
    task = client._parse_task(page)
    assert task.title == "EEGレポートを書く"
    assert task.status == "In progress"
    assert task.due == "2026-06-24"
    assert "EEGレポートを書く" in task.as_todo()


def test_create_task_uses_data_source_parent() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/v1/pages"
        body = json_body(request)
        assert body["parent"]["type"] == "data_source_id"
        assert body["properties"]["Task name"]["title"][0]["text"]["content"] == "次回Todo"
        assert body["properties"]["Parent-task"]["relation"][0]["id"] == "parent-id"
        return httpx.Response(
            200,
            json={
                "id": "task-id",
                "url": "https://notion.so/task-id",
                "properties": {
                    "Task name": {"type": "title", "title": [{"plain_text": "次回Todo"}]},
                    "Status": {"type": "status", "status": {"name": "Not Started"}},
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notion = NotionClient("secret", "tasksource", client=client)
    task = notion.create_task("次回Todo", parent_task_id="parent-id")
    assert task is not None
    assert task.title == "次回Todo"
    assert requests


def json_body(request: httpx.Request) -> dict:
    import json

    return json.loads(request.content.decode("utf-8"))


def test_fetch_tasks_resolves_database_id_to_data_source_id() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/v1/data_sources/databaseid":
            return httpx.Response(404, json={"message": "not found"})
        if request.method == "GET" and request.url.path == "/v1/databases/databaseid":
            return httpx.Response(200, json={"data_sources": [{"id": "data-source-id"}]})
        if request.method == "POST" and request.url.path == "/v1/data_sources/datasourceid/query":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "task-id",
                            "url": "https://notion.so/task-id",
                            "properties": {
                                "Task name": {"type": "title", "title": [{"plain_text": "Resolved task"}]},
                                "Status": {"type": "status", "status": {"name": "Not Started"}},
                            },
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notion = NotionClient("secret", "database-id", client=client)

    tasks = notion.fetch_open_tasks()

    assert tasks[0].title == "Resolved task"
    assert notion.tasks_data_source_id == "datasourceid"
    assert [request.url.path for request in requests] == [
        "/v1/data_sources/databaseid",
        "/v1/databases/databaseid",
        "/v1/data_sources/datasourceid/query",
    ]
