"""版本定界血缘的核心行为测试。

直接构造服务实例、复用同一份内存仓库，覆盖：
- 报告复现与整改核对各自取边，不混拼；
- 迟到补录、撤销、重登记的双时间语义；
- 同一时点重复查询不重复返回路径；
- 按原记录定位全部版本；
- 图谱比对消费的记录与原报告可复现路径一致，并记录对照请求日期。
"""
from __future__ import annotations

import pytest

from app.services.data_lineage import DataLineageService
from app.store import Store, store


@pytest.fixture()
def service() -> DataLineageService:
    # 每次重建仓库，避免用例之间互相污染内存台账。
    fresh = Store()
    store._tables = fresh._tables
    return DataLineageService()


REPORT_TIME = "2026-11-01"
START = "ODS订单"
END = "ADS销售报表"


def _displays(mode_result: dict) -> list[str]:
    return sorted(path["display"] for path in mode_result["paths"])


def test_report_mode_excludes_late_backfilled_edge(service: DataLineageService) -> None:
    result = service.versioned_query(as_of=REPORT_TIME, start=START, end=END, mode="report")
    paths = result["paths"]
    # 迟到补录的 DATA-1004 声明生效 2026-10-20，但记录时间 2026-11-18；
    # 报告复现只能看到当时已登记的 4 条边，不能拼成直连路径。
    assert "ODS订单 → DWD订单明细 → ADS销售报表" not in _displays(result)
    assert 7 not in result["record_ids"]
    assert set(result["record_ids"]) == {4, 5, 6, 8}
    assert len(paths) == 2


def test_remediation_mode_includes_historical_fact(service: DataLineageService) -> None:
    result = service.versioned_query(as_of=REPORT_TIME, start=START, end=END, mode="remediation")
    # 按如今已知事实，迟到关系在报告时点已生效，整改口径可见；
    # 但报告时点可见、后来被撤销的 DATA-1005 不可见。
    assert "ODS订单 → DWD订单明细 → ADS销售报表" in _displays(result)
    assert 7 in result["record_ids"]
    assert 8 not in result["record_ids"]


def test_modes_never_mix_results(service: DataLineageService) -> None:
    result = service.versioned_query(as_of=REPORT_TIME, start=START, end=END, mode="both")
    report = set(result["modes"]["report"]["record_ids"])
    remediation = set(result["modes"]["remediation"]["record_ids"])
    assert 7 in remediation and 7 not in report
    assert 8 in report and 8 not in remediation
    # 两种用途各自成图，任何一条路径都不能同时引用对方专属的边。
    for path in result["modes"]["report"]["paths"]:
        assert set(path["record_ids"]) <= report
    for path in result["modes"]["remediation"]["paths"]:
        assert set(path["record_ids"]) <= remediation


def test_repeated_query_at_same_as_of_dedupes(service: DataLineageService) -> None:
    first = service.versioned_query(as_of=REPORT_TIME, start=START, end=END, mode="both")
    second = service.versioned_query(as_of=REPORT_TIME, start=START, end=END, mode="both")
    assert first["query_id"] == second["query_id"]
    assert second["reused"] is True
    assert second["duplicate_requests"] == 1
    assert first["path_ids"] == second["path_ids"]
    # 路径 ID 唯一，没有重复返回同一路径。
    assert len(second["path_ids"]) == len(set(second["path_ids"]))


def test_withdrawn_and_superseded_versions_are_located_by_original_record(
    service: DataLineageService,
) -> None:
    result = service.locate_versions(9)
    assert result is not None
    assert result["chain"] == "DATA-1006"
    assert result["current_record_id"] == 10
    assert [row["id"] for row in result["versions"]] == [9, 10]
    assert result["versions"][0]["已被取代"] is True
    assert result["versions"][0]["已撤销"] is True
    assert result["versions"][1]["当前版本"] is True

    # 用注册后的版本也能回指同一族的原记录。
    assert service.locate_versions(10)["lineage_key"] == 9


def test_backfill_is_query_only_by_mode(service: DataLineageService) -> None:
    entry, message = service.backfill_edge(
        {
            "上游实体": "ODS订单",
            "下游实体": "DWS销售汇总",
            "生效时间": "2026-10-28",
            "记录时间": "2026-11-22",
        }
    )
    assert entry is not None, message
    assert entry["登记方式"] == "补录"
    result = service.versioned_query(as_of=REPORT_TIME, start=START, end=END, mode="both")
    # 补录边在报告口径不可见，在整改口径可见。
    assert entry["id"] not in result["modes"]["report"]["record_ids"]
    assert entry["id"] in result["modes"]["remediation"]["record_ids"]


def test_backfill_rejects_future_effective_date(service: DataLineageService) -> None:
    entry, message = service.backfill_edge(
        {
            "上游实体": "A",
            "下游实体": "B",
            "生效时间": "2099-01-01",
            "记录时间": "2026-11-22",
        }
    )
    assert entry is None
    assert "生效时间不能晚于记录时间" in message


def test_compare_consumes_report_reproducible_edges(service: DataLineageService) -> None:
    comparison = service.versioned_compare(as_of=REPORT_TIME, start=START, end=END)
    base_ids = set(comparison["verification"]["consumed_record_ids"])
    report = service.versioned_query(as_of=REPORT_TIME, start=START, end=END, mode="report")
    assert base_ids == set(report["record_ids"])
    assert comparison["verification"]["consistent"] is True
    # 对照请求使用的日期随任务存下，双方取数时点分别标注。
    assert comparison["task"]["对照请求日期"] == REPORT_TIME
    assert comparison["task"]["基准取数时点"] == REPORT_TIME
    assert comparison["task"]["对比取数时点"] == comparison["request_date"]
    assert comparison["task"]["对比取数时点"] >= comparison["task"]["基准取数时点"]
    assert comparison["base"]["record_cutoff"] == REPORT_TIME
    assert comparison["target"]["record_cutoff"] == comparison["request_date"]
    # 真实差异：迟到边是新增关系，撤销边是消失关系。
    added = {edge["记录ID"] for edge in comparison["diff"]["added_edges"]}
    removed = {edge["记录ID"] for edge in comparison["diff"]["removed_edges"]}
    assert 7 in added
    assert 8 in removed


def test_compare_task_is_reused_for_same_basis(service: DataLineageService) -> None:
    first = service.versioned_compare(as_of=REPORT_TIME, start=START, end=END)
    second = service.versioned_compare(as_of=REPORT_TIME, start=START, end=END)
    assert first["task"]["任务编号"] == second["task"]["任务编号"]
    assert second["task_reused"] is True
    assert len(service.compare_tasks()) == 1


def test_invalid_query_arguments_raise(service: DataLineageService) -> None:
    with pytest.raises(ValueError):
        service.versioned_query(as_of="2026/11/01", start=START, end=END)
    with pytest.raises(ValueError):
        service.versioned_query(as_of=REPORT_TIME, start="", end=END)
    with pytest.raises(ValueError):
        service.versioned_query(as_of=REPORT_TIME, start=START, end=END, mode="guess")
