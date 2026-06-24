from __future__ import annotations

from typing import Any

import numpy as np

from .db import Database
from .rag import RagStore


def build_rag_graph(database: Database, rag: RagStore, session_id: str | None = None) -> dict[str, Any]:
    rows = database.query(
        """
        SELECT
            episodes.id,
            episodes.session_id,
            episodes.started_at,
            episodes.ended_at,
            episodes.label,
            episodes.severity,
            episodes.active_window,
            episodes.work_summary,
            episodes.embedding_ref,
            eeg_windows.engagement,
            eeg_windows.workload
        FROM episodes
        LEFT JOIN eeg_windows ON eeg_windows.id = episodes.eeg_window_id
        WHERE (? IS NULL OR episodes.session_id = ?)
        ORDER BY episodes.session_id, episodes.started_at, episodes.id
        """,
        (session_id, session_id),
    )
    if not rows:
        return {
            "session_id": session_id,
            "embedding_backend": "chroma" if rag.collection is not None else "none",
            "node_count": 0,
            "edge_count": 0,
            "nodes": [],
            "edges": [],
        }

    embeddings = rag.embeddings_for_ids([str(row["embedding_ref"]) for row in rows])
    embedded_rows = [row for row in rows if str(row["embedding_ref"]) in embeddings]
    coordinates = _project_embeddings([embeddings[str(row["embedding_ref"])] for row in embedded_rows])
    coordinate_by_ref = {
        str(row["embedding_ref"]): coordinate
        for row, coordinate in zip(embedded_rows, coordinates)
    }

    nodes = []
    for row in rows:
        coordinate = coordinate_by_ref.get(str(row["embedding_ref"]), (0.0, 0.0, 0.0))
        nodes.append(
            {
                "id": str(row["embedding_ref"]),
                "episode_id": int(row["id"]),
                "session_id": str(row["session_id"]),
                "started_at": str(row["started_at"]),
                "ended_at": str(row["ended_at"]),
                "label": str(row["label"]),
                "severity": str(row["severity"]),
                "active_window": str(row["active_window"]),
                "summary": str(row["work_summary"]),
                "engagement": _optional_float(row.get("engagement")),
                "workload": _optional_float(row.get("workload")),
                "x": coordinate[0],
                "y": coordinate[1],
                "z": coordinate[2],
                "has_embedding": str(row["embedding_ref"]) in coordinate_by_ref,
            }
        )

    edges = []
    rows_by_session: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_session.setdefault(str(row["session_id"]), []).append(row)
    for session_rows in rows_by_session.values():
        for source, target in zip(session_rows, session_rows[1:]):
            source_engagement = _optional_float(source.get("engagement"))
            target_engagement = _optional_float(target.get("engagement"))
            source_workload = _optional_float(source.get("workload"))
            target_workload = _optional_float(target.get("workload"))
            focus_delta = None
            if source_engagement is not None and target_engagement is not None:
                focus_delta = target_engagement - source_engagement
            workload_delta = None
            if source_workload is not None and target_workload is not None:
                workload_delta = target_workload - source_workload
            edges.append(
                {
                    "id": f"{source['embedding_ref']}->{target['embedding_ref']}",
                    "source": str(source["embedding_ref"]),
                    "target": str(target["embedding_ref"]),
                    "session_id": str(source["session_id"]),
                    "focus_delta": focus_delta,
                    "focus_up": bool(focus_delta is not None and focus_delta > 0),
                    "workload_delta": workload_delta,
                    "workload_up": bool(workload_delta is not None and workload_delta > 0),
                }
            )

    return {
        "session_id": session_id,
        "embedding_backend": "chroma" if rag.collection is not None else "none",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


def _project_embeddings(embeddings: list[list[float]]) -> list[tuple[float, float, float]]:
    if not embeddings:
        return []
    matrix = np.asarray(embeddings, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        return [(0.0, 0.0, 0.0) for _ in embeddings]
    if matrix.shape[0] == 1:
        return [(0.0, 0.0, 0.0)]

    centered = matrix - matrix.mean(axis=0, keepdims=True)
    _, _, components_t = np.linalg.svd(centered, full_matrices=False)
    projected = centered @ components_t[: min(3, components_t.shape[0])].T
    if projected.shape[1] < 3:
        projected = np.pad(projected, ((0, 0), (0, 3 - projected.shape[1])))

    scale = float(np.max(np.linalg.norm(projected, axis=1)))
    if scale > 0:
        projected = projected / scale
    return [tuple(float(value) for value in row[:3]) for row in projected]


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
