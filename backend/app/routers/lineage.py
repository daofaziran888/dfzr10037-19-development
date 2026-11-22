"""数据血缘的版本定界查询入口。

这些固定路径必须早于通用资源路由注册，否则会被 `/api/{module}/{entry_id}`
的动态路径抢先匹配。用途只有两种：``report`` 复现原报告当时可见的路径，
``remediation`` 按如今已知的历史事实核对；两者各自取边，不混拼路径。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import service_for
from app.services.data_lineage import DataLineageService, VALID_MODES

router = APIRouter(prefix="/api/data_lineage", tags=["数据血缘"])


def _service() -> DataLineageService:
    service = service_for("data_lineage")
    if not isinstance(service, DataLineageService):
        raise HTTPException(status_code=500, detail="数据血缘服务未注册")
    return service


class VersionedQueryPayload(BaseModel):
    as_of: str = Field(description="报告取数时点，YYYY-MM-DD")
    start: str = Field(description="起始实体")
    end: str = Field(description="目标实体")
    mode: str = Field(default="both", description="report / remediation / both")
    max_depth: int = 6
    sync_compare: bool = False


class BackfillPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class VersionedComparePayload(BaseModel):
    as_of: str = Field(description="报告取数时点，YYYY-MM-DD")
    start: str = Field(description="起始实体")
    end: str = Field(description="目标实体")
    max_depth: int = 6


@router.post("/query")
def versioned_query(payload: VersionedQueryPayload) -> dict[str, Any]:
    service = _service()
    try:
        result = service.versioned_query(
            as_of=payload.as_of,
            start=payload.start,
            end=payload.end,
            mode=payload.mode,
            max_depth=payload.max_depth,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.sync_compare:
        comparison = service.versioned_compare(
            as_of=payload.as_of,
            start=payload.start,
            end=payload.end,
            max_depth=payload.max_depth,
        )
        result["comparison"] = comparison
    return result


@router.get("/query")
def versioned_query_get(
    as_of: str = Query(description="报告取数时点，YYYY-MM-DD"),
    start: str = Query(description="起始实体"),
    end: str = Query(description="目标实体"),
    mode: str = Query(default="both"),
    max_depth: int = Query(default=6, ge=1, le=12),
) -> dict[str, Any]:
    service = _service()
    try:
        return service.versioned_query(
            as_of=as_of, start=start, end=end, mode=mode, max_depth=max_depth
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/backfill")
def backfill_edge(payload: BackfillPayload) -> dict[str, Any]:
    service = _service()
    entry, message = service.backfill_edge(payload.values)
    return {"ok": entry is not None, "message": message, "entry": entry}


@router.post("/compare")
def versioned_compare(payload: VersionedComparePayload) -> dict[str, Any]:
    service = _service()
    try:
        return service.versioned_compare(
            as_of=payload.as_of,
            start=payload.start,
            end=payload.end,
            max_depth=payload.max_depth,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/compare/tasks")
def compare_tasks() -> dict[str, Any]:
    service = _service()
    tasks = service.compare_tasks()
    return {"items": tasks, "total": len(tasks)}


@router.get("/versions/{record_id}")
def locate_versions(record_id: int, chain: str = Query(default="")) -> dict[str, Any]:
    service = _service()
    result = service.locate_versions(record_id, chain)
    if result is None:
        raise HTTPException(status_code=404, detail=f"血缘记录 {record_id} 不存在或已归档")
    return result


@router.get("/modes")
def lineage_modes() -> dict[str, Any]:
    return {
        "modes": [
            {"key": "report", "label": "报告复现（当时可见）"},
            {"key": "remediation", "label": "整改核对（如今已知）"},
        ],
        "valid": list(VALID_MODES),
    }
