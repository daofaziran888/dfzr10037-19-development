"""数据血缘领域服务。

除了常规的血缘链路维护，这里承载「版本定界血缘」能力：

- 每条链路同时有生效时间（valid time）和记录时间（transaction time）；
- 提供从某条链路 / 某个报告时点出发的定界查询入口；
- 支持两种用途：``report``（报告复现，当时能看到什么）和
  ``remediation``（整改核对，按如今已知的历史事实是什么）；
- 查询结果可同步到图谱比对待办，比对基于版本定界数据生成真实影响差异；
- 被撤销或重登记的关系可以按原记录 ID 定位到全部版本。

核心约束：两种用途各自独立取边，绝不会把「原报告可见的关系」和
「后来补录的关系」拼成同一条确定路径；同一时点的连续查询不会重复返回路径。
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Iterable

from app.services.base import BaseModuleService
from app.store import store

MODE_LABELS = {
    "report": "报告复现（当时可见）",
    "remediation": "整改核对（如今已知）",
}
VALID_MODES = tuple(MODE_LABELS)


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _iso(value: date | None) -> str:
    return value.isoformat() if value else ""


def _valid_from(row: dict[str, Any]) -> date:
    parsed = _parse_date(row.get("生效时间")) or _parse_date(row.get("最后更新"))
    return parsed or date.min


def _valid_to(row: dict[str, Any]) -> date | None:
    return _parse_date(row.get("失效时间"))


def _recorded_at(row: dict[str, Any]) -> date:
    parsed = _parse_date(row.get("记录时间")) or _parse_date(row.get("生效时间"))
    return parsed or date.min


def _retired_at(row: dict[str, Any]) -> date | None:
    return _parse_date(row.get("撤销时间"))


def _is_withdrawn(row: dict[str, Any]) -> bool:
    return bool(_retired_at(row)) or str(row.get("status", "")) == "已废弃"


def _lineage_key(row: dict[str, Any]) -> int:
    try:
        return int(row.get("原记录ID") or row.get("id") or 0)
    except (TypeError, ValueError):
        return int(row.get("id") or 0)


def _record_id(row: dict[str, Any]) -> int:
    try:
        return int(row.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _late_arriving(row: dict[str, Any]) -> bool:
    """声明生效日早于实际登记日，即为迟到关系。"""
    return _recorded_at(row) > _valid_from(row)


def _normalize(text: Any) -> str:
    return str(text or "").strip()


class DataLineageService(BaseModuleService):
    MODULE = "data_lineage"
    ENTITY = "血缘链路"
    OPERATION_TERM = "转换"
    LIST_FIELDS = ["链路编号", "上游实体", "下游实体", "转换描述", "关联字段", "影响层级", "链路类型", "最后更新"]
    REQUIRED_FIELDS = ["链路编号", "上游实体", "下游实体"]
    STATUS_ORDER = ["自动推导", "已确认", "待验证", "已废弃"]
    ACTION_RULES = {"确认链路": "已确认", "提交验证": "待验证", "废弃链路": "已废弃"}
    NEGATIVE_ACTIONS = []

    # 版本定界查询 / 比对的内存台账；图谱比对待办本身落 store，便于页面直接查看。
    def __init__(self) -> None:
        self._query_log: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._query_seq = 0
        self._compare_task_by_fingerprint: dict[str, dict[str, Any]] = {}

    # ===== 跨模块数据流实现 =====
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry, message = super().run_action(entry_id, action)
        if entry is None:
            return entry, message
        # 链路 L12: data_lineage → graph_evolve（血缘变更记录触发演化记录）
        if entry.get("status") in ("已确认", "待验证"):
            evolve_msg = self._push_to_graph_evolve(entry)
            message += "；" + evolve_msg
        # 链路 L13: data_lineage → graph_compare（血缘链路触发图谱版本比对）
        if entry.get("status") in ("已确认", "待验证"):
            compare_msg = self._push_to_graph_compare(entry)
            message += "；" + compare_msg
        return entry, message

    def _push_to_graph_evolve(self, entry: dict[str, Any]) -> str:
        rows = store.rows("graph_evolve")
        next_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        ev_entry: dict[str, Any] = {
            "id": next_id,
            "status": "已记录",
            "pending": True,
            "abnormal": False,
            "记录编号": f"GRAP-EVOL-{next_id:04d}",
            "变更类型": f"lineage-{entry.get('链路类型','')}",
            "影响实体": str(entry.get("下游实体", "")),
            "影响关系": str(entry.get("关联字段", "")),
            "版本标签": f"v{next_id}.0",
            "变更描述": f"血缘链路{entry.get('链路编号','')}已变更",
            "回滚版本": f"v{next_id-1}.0",
            "演化时间": str(entry.get("最后更新", "2026-11-18")),
        }
        rows.append(ev_entry)
        return f"已记录图谱演化 {ev_entry['记录编号']}"

    def _push_to_graph_compare(self, entry: dict[str, Any]) -> str:
        rows = store.rows("graph_compare")
        next_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        cmp_entry: dict[str, Any] = {
            "id": next_id,
            "status": "待执行",
            "pending": True,
            "abnormal": False,
            "任务编号": f"GRAP-CMP-{next_id:04d}",
            "基准图": str(entry.get("上游实体", "")),
            "对比图": str(entry.get("下游实体", "")),
            "比对维度": "lineage-change-diff",
            "新增实体": "auto-calc",
            "删除实体": "auto-calc",
            "变更关系": "auto-calc",
            "比对时间": str(entry.get("最后更新", "2026-11-18")),
        }
        rows.append(cmp_entry)
        return f"已触发图谱比对 {cmp_entry['任务编号']}"

    # ===== 版本定界：取边 =====
    def _all_rows(self) -> list[dict[str, Any]]:
        return list(store.rows(self.MODULE))

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """普通登记也补齐双时间字段，避免新链路在定界查询里缺时间而不可见。"""
        entry, missing = super().create_entry(values)
        if entry is None:
            return entry, missing
        effective = _parse_date(values.get("生效时间")) or _parse_date(
            values.get("最后更新")
        ) or date.today()
        recorded = _parse_date(values.get("记录时间")) or date.today()
        entry["生效时间"] = _iso(effective)
        entry["记录时间"] = _iso(recorded)
        entry["失效时间"] = _iso(_parse_date(values.get("失效时间")))
        entry["撤销时间"] = ""
        entry.update(
            {
                "版本": 1,
                "原记录ID": _record_id(entry),
                "取代记录ID": "",
                "登记方式": "补录" if recorded > effective else "初始登记",
            }
        )
        return entry, []

    def _superseders(self, rows: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        superseders: dict[int, dict[str, Any]] = {}
        for row in rows:
            reference = _normalize(row.get("取代记录ID"))
            if not reference:
                continue
            try:
                superseders[int(reference)] = row
            except ValueError:
                continue
        return superseders

    def _visible_in_report(
        self, row: dict[str, Any], as_of: date, superseders: dict[int, dict[str, Any]]
    ) -> bool:
        """报告复现：生效时间与记录时间都截断在报告时点。"""
        if _valid_from(row) > as_of:
            return False
        valid_to = _valid_to(row)
        if valid_to is not None and as_of >= valid_to:
            return False
        if _recorded_at(row) > as_of:
            return False
        retired = _retired_at(row)
        if retired is not None and as_of >= retired:
            return False
        if _is_withdrawn(row) and retired is None:
            return False
        superseder = superseders.get(_record_id(row))
        if superseder is not None:
            if _recorded_at(superseder) <= as_of and not self._withdrawn_at(superseder, as_of):
                return False
        return True

    def _visible_in_remediation(
        self, row: dict[str, Any], as_of: date, superseded_ids: set[int]
    ) -> bool:
        """整改核对：按如今已知事实，只受生效时间约束，不看登记时间。"""
        if _visible_valid_only(row, as_of):
            return False
        if _is_withdrawn(row):
            return False
        if _record_id(row) in superseded_ids:
            return False
        return True

    def _withdrawn_at(self, row: dict[str, Any], as_of: date) -> bool:
        retired = _retired_at(row)
        if retired is not None:
            return as_of >= retired
        return _is_withdrawn(row)

    def eligible_rows(self, as_of: date, mode: str) -> list[dict[str, Any]]:
        rows = self._all_rows()
        superseders = self._superseders(rows)
        superseded_ids = set(superseders)
        if mode == "report":
            candidates = [row for row in rows if self._visible_in_report(row, as_of, superseders)]
        elif mode == "remediation":
            candidates = [row for row in rows if self._visible_in_remediation(row, as_of, superseded_ids)]
        else:
            raise ValueError(f"未知的查询用途：{mode}")
        return sorted(candidates, key=_edge_sort_key)

    def excluded_rows(self, as_of: date, mode: str) -> list[dict[str, Any]]:
        """给出「另一用途里成立、但本用途被排除」的链路，便于解释日期冲突。"""
        rows = self._all_rows()
        superseders = self._superseders(rows)
        superseded_ids = set(superseders)
        excluded: list[dict[str, Any]] = []
        if mode == "report":
            # 报告口径漏掉的，是如今按历史事实成立、但报告时点尚未登记的关系
            for row in rows:
                if self._visible_in_report(row, as_of, superseders):
                    continue
                if self._visible_in_remediation(row, as_of, superseded_ids):
                    excluded.append(row)
        else:
            # 整改口径剔除的，是报告当时能看到、如今已撤销或被取代的关系
            for row in rows:
                if self._visible_in_remediation(row, as_of, superseded_ids):
                    continue
                if self._visible_in_report(row, as_of, superseders):
                    excluded.append(row)
        return sorted(excluded, key=_edge_sort_key)

    # ===== 版本定界：找路径 =====
    def _build_adjacency(self, edges: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        adjacency: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            upstream = _normalize(edge.get("上游实体"))
            if not upstream:
                continue
            adjacency.setdefault(upstream, []).append(edge)
        return adjacency

    def find_paths(
        self,
        *,
        start: str,
        end: str,
        as_of: date,
        mode: str,
        max_depth: int = 6,
    ) -> dict[str, Any]:
        start = _normalize(start)
        end = _normalize(end)
        edges = self.eligible_rows(as_of, mode)
        adjacency = self._build_adjacency(edges)
        paths: list[dict[str, Any]] = []
        seen_signatures: set[tuple[Any, ...]] = set()

        def walk(
            node: str,
            visited: set[str],
            used: list[dict[str, Any]],
        ) -> None:
            if node == end and used:
                signature = tuple(_record_id(edge) for edge in used)
                if signature in seen_signatures:
                    return
                seen_signatures.add(signature)
                paths.append(self._path_payload(used, as_of))
                return
            if len(used) >= max_depth:
                return
            for edge in adjacency.get(node, []):
                downstream = _normalize(edge.get("下游实体"))
                if not downstream or downstream in visited:
                    continue
                walk(downstream, visited | {downstream}, [*used, edge])

        if start and end and start != end:
            walk(start, {start}, [])
        paths.sort(key=lambda item: (len(item["steps"]), item["path_id"]))
        return {
            "mode": mode,
            "label": MODE_LABELS[mode],
            "as_of": _iso(as_of),
            "start": start,
            "end": end,
            "max_depth": max_depth,
            "path_count": len(paths),
            "paths": paths,
            "path_ids": [item["path_id"] for item in paths],
            "record_ids": sorted({step["记录ID"] for item in paths for step in item["steps"]}),
            "edge_count": len(edges),
        }

    def _path_payload(self, used: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
        steps = [self._step_payload(edge, as_of) for edge in used]
        nodes = [steps[0]["上游实体"], *(step["下游实体"] for step in steps)]
        signature = "|".join(str(step["记录ID"]) for step in steps) + "::" + "->".join(nodes)
        digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]
        return {
            "path_id": f"LP-{digest}",
            "nodes": nodes,
            "display": " → ".join(nodes),
            "hop_count": len(steps),
            "record_ids": [step["记录ID"] for step in steps],
            "chain_codes": [step["链路编号"] for step in steps],
            "steps": steps,
            "late_edge_count": sum(1 for step in steps if step["迟到补录"]),
        }

    @staticmethod
    def _step_payload(edge: dict[str, Any], as_of: date) -> dict[str, Any]:
        return {
            "记录ID": _record_id(edge),
            "链路编号": _normalize(edge.get("链路编号")),
            "上游实体": _normalize(edge.get("上游实体")),
            "下游实体": _normalize(edge.get("下游实体")),
            "转换描述": _normalize(edge.get("转换描述")),
            "生效时间": _iso(_parse_date(edge.get("生效时间"))),
            "记录时间": _iso(_parse_date(edge.get("记录时间"))),
            "版本": int(edge.get("版本") or 1),
            "状态": _normalize(edge.get("status")),
            "迟到补录": _late_arriving(edge) or _normalize(edge.get("登记方式")) == "补录",
            "报告时点后登记": _recorded_at(edge) > as_of,
        }

    # ===== 版本定界：查询入口 =====
    def versioned_query(
        self,
        *,
        as_of: str,
        start: str = "",
        end: str = "",
        mode: str = "both",
        max_depth: int = 6,
    ) -> dict[str, Any]:
        as_of_date = _parse_date(as_of)
        if as_of_date is None:
            raise ValueError("报告时点格式应为 YYYY-MM-DD")
        if mode not in ("both", *VALID_MODES):
            raise ValueError(f"用途只能是 {'/'.join(('both', *VALID_MODES))}")
        if not _normalize(start) or not _normalize(end):
            raise ValueError("起始实体和目标实体不能为空")
        try:
            depth = int(max_depth)
        except (TypeError, ValueError):
            depth = 6
        depth = max(1, min(depth, 12))

        modes = VALID_MODES if mode == "both" else (mode,)
        payloads: dict[str, Any] = {}
        for current in modes:
            payloads[current] = self.find_paths(
                start=start, end=end, as_of=as_of_date, mode=current, max_depth=depth
            )
            payloads[current]["excluded_edges"] = [
                self._edge_explanation(row, as_of_date, current)
                for row in self.excluded_rows(as_of_date, current)
            ]

        key = (
            mode,
            _iso(as_of_date),
            _normalize(start),
            _normalize(end),
            depth,
        )
        entry = self._query_log.get(key)
        if entry is None:
            self._query_seq += 1
            entry = {
                "query_id": f"LQ-{self._query_seq:04d}",
                "mode": mode,
                "as_of": _iso(as_of_date),
                "start": _normalize(start),
                "end": _normalize(end),
                "max_depth": depth,
                "attempts": 0,
                "duplicate_ignored": 0,
            }
            self._query_log[key] = entry
        entry["attempts"] += 1
        reused = entry["attempts"] > 1
        if reused:
            entry["duplicate_ignored"] += 1

        primary = payloads.get("report") or payloads[modes[0]]
        # 台账记下本次真正返回的路径，便于核验重复请求没有多返回路径。
        entry["last_path_ids"] = primary["path_ids"]
        entry["last_record_ids"] = primary["record_ids"]
        return {
            "query_id": entry["query_id"],
            "mode": mode,
            "as_of": _iso(as_of_date),
            "start": _normalize(start),
            "end": _normalize(end),
            "max_depth": depth,
            "reused": reused,
            "duplicate_requests": entry["duplicate_ignored"],
            "modes": payloads,
            "paths": primary["paths"],
            "path_ids": primary["path_ids"],
            "record_ids": primary["record_ids"],
            "path_count": primary["path_count"],
            "labels": {key_: MODE_LABELS[key_] for key_ in modes},
        }

    @staticmethod
    def _edge_explanation(edge: dict[str, Any], as_of: date, mode: str) -> dict[str, Any]:
        step = DataLineageService._step_payload(edge, as_of)
        if mode == "report":
            step["排除原因"] = "报告时点之后才登记（迟到补录），复现原报告时不可用"
        else:
            step["排除原因"] = (
                "已被撤销" if _is_withdrawn(edge) else "已被重登记版本取代"
            )
        return step

    # ===== 迟到补录 =====
    def backfill_edge(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        upstream = _normalize(values.get("上游实体"))
        downstream = _normalize(values.get("下游实体"))
        effective = _parse_date(values.get("生效时间"))
        if not upstream or not downstream:
            return None, "上游实体和目标实体不能为空"
        if effective is None:
            return None, "生效时间格式应为 YYYY-MM-DD"
        recorded = _parse_date(values.get("记录时间")) or date.today()
        if effective > recorded:
            return None, "生效时间不能晚于记录时间：迟到补录必须在登记日前已经生效"
        if upstream == downstream:
            return None, "上游实体和目标实体不能相同"

        rows = store.rows(self.MODULE)
        chain = _normalize(values.get("链路编号"))
        for row in rows:
            if _normalize(row.get("上游实体")) != upstream:
                continue
            if _normalize(row.get("下游实体")) != downstream:
                continue
            if _parse_date(row.get("生效时间")) != effective:
                continue
            if _parse_date(row.get("记录时间")) != recorded:
                continue
            # 没显式指定链路编号时，同一对实体 + 同一生效/记录时间视为同一条补录。
            if chain and _normalize(row.get("链路编号")) != chain:
                continue
            return row, "相同链路在相同生效/记录时间已登记，未重复补录"

        next_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        chain = chain or self._next_chain_code(upstream, downstream, effective, next_id)
        entry: dict[str, Any] = {
            "id": next_id,
            "status": _normalize(values.get("status")) or "待验证",
            "pending": True,
            "abnormal": False,
            "链路编号": chain,
            "上游实体": upstream,
            "下游实体": downstream,
            "转换描述": _normalize(values.get("转换描述")) or "迟到补录的血缘转换",
            "关联字段": _normalize(values.get("关联字段")),
            "影响层级": _normalize(values.get("影响层级")) or f"{upstream}→{downstream}",
            "链路类型": _normalize(values.get("链路类型")) or "补录",
            "最后更新": _iso(recorded),
            "生效时间": _iso(effective),
            "记录时间": _iso(recorded),
            "失效时间": "",
            "撤销时间": "",
            "版本": 1,
            "原记录ID": next_id,
            "取代记录ID": "",
            "登记方式": "补录",
        }
        rows.append(entry)
        return entry, (
            f"已补录链路 {chain}：生效 {_iso(effective)}，登记 {_iso(recorded)}"
        )

    def _next_chain_code(
        self, upstream: str, downstream: str, effective: date, record_id: int
    ) -> str:
        # 用记录 ID 保证同一进程内多次补录不会撞号。
        digest = hashlib.sha1(
            f"{upstream}|{downstream}|{_iso(effective)}|{record_id}".encode("utf-8")
        ).hexdigest()[:6].upper()
        return f"DATA-BF-{digest}"

    # ===== 版本定位 =====
    def locate_versions(self, record_id: int, chain: str = "") -> dict[str, Any] | None:
        rows = self._all_rows()
        target = next((row for row in rows if _record_id(row) == record_id), None)
        if target is None and chain:
            target = next(
                (row for row in rows if _normalize(row.get("链路编号")) == _normalize(chain)),
                None,
            )
        if target is None:
            return None
        key = _lineage_key(target)
        chain_code = _normalize(target.get("链路编号"))
        versions = [
            row
            for row in rows
            if _lineage_key(row) == key or (
                chain_code and _normalize(row.get("链路编号")) == chain_code
            )
        ]
        versions.sort(key=lambda row: (int(row.get("版本") or 1), _recorded_at(row), _record_id(row)))
        superseded_ids = set(self._superseders(rows))
        payload = []
        current_record_id = 0
        for row in versions:
            current = _record_id(row) not in superseded_ids and not _is_withdrawn(row)
            if current:
                current_record_id = _record_id(row)
            payload.append(
                {
                    **row,
                    "当前版本": current,
                    "已撤销": _is_withdrawn(row),
                    "已被取代": _record_id(row) in superseded_ids,
                }
            )
        return {
            "found": True,
            "record_id": record_id,
            "chain": chain_code,
            "lineage_key": key,
            "current_record_id": current_record_id,
            "versions": payload,
            "version_count": len(payload),
        }

    # ===== 图谱比对：按版本定界数据生成真实差异 =====
    def versioned_compare(
        self,
        *,
        as_of: str,
        start: str,
        end: str,
        max_depth: int = 6,
    ) -> dict[str, Any]:
        query = self.versioned_query(
            as_of=as_of, start=start, end=end, mode="both", max_depth=max_depth
        )
        report = query["modes"]["report"]
        remediation = query["modes"]["remediation"]

        report_path_ids = set(report["path_ids"])
        remediation_path_ids = set(remediation["path_ids"])
        report_edges = set(report["record_ids"])
        remediation_edges = set(remediation["record_ids"])

        added_paths = [p for p in remediation["paths"] if p["path_id"] not in report_path_ids]
        removed_paths = [p for p in report["paths"] if p["path_id"] not in remediation_path_ids]
        added_edge_ids = sorted(remediation_edges - report_edges)
        removed_edge_ids = sorted(report_edges - remediation_edges)
        added_edges = [self._find_edge_payload(edge_id, as_of) for edge_id in added_edge_ids]
        removed_edges = [self._find_edge_payload(edge_id, as_of) for edge_id in removed_edge_ids]

        fingerprint = "|".join(
            [
                query["as_of"],
                query["start"],
                query["end"],
                str(query["max_depth"]),
                ",".join(map(str, sorted(report_edges))),
                ",".join(map(str, sorted(remediation_edges))),
            ]
        )
        existing = self._compare_task_by_fingerprint.get(fingerprint)
        reused = existing is not None
        task = existing or self._create_compare_task(
            query=query,
            added_edges=added_edges,
            removed_edges=removed_edges,
            added_count=len(added_edge_ids),
            removed_count=len(removed_edge_ids),
        )
        self._compare_task_by_fingerprint.setdefault(fingerprint, task)

        consistent, reproduced = self._verify_report_reproduction(
            report=report, as_of=query["as_of"], max_depth=query["max_depth"]
        )
        today = _iso(date.today())
        return {
            "ok": True,
            "task": task,
            "task_reused": reused,
            "as_of": query["as_of"],
            "request_date": today,
            "query_id": query["query_id"],
            "base": {
                "mode": "report",
                "label": MODE_LABELS["report"],
                "as_of": query["as_of"],
                "record_cutoff": query["as_of"],
                "knowledge_cutoff": query["as_of"],
                "path_count": report["path_count"],
                "path_ids": report["path_ids"],
                "record_ids": report["record_ids"],
                "paths": report["paths"],
            },
            "target": {
                "mode": "remediation",
                "label": MODE_LABELS["remediation"],
                "as_of": query["as_of"],
                "record_cutoff": today,
                "knowledge_cutoff": today,
                "path_count": remediation["path_count"],
                "path_ids": remediation["path_ids"],
                "record_ids": remediation["record_ids"],
                "paths": remediation["paths"],
            },
            "diff": {
                "added_paths": added_paths,
                "removed_paths": removed_paths,
                "added_edges": added_edges,
                "removed_edges": removed_edges,
                "added_path_count": len(added_paths),
                "removed_path_count": len(removed_paths),
                "added_edge_count": len(added_edges),
                "removed_edge_count": len(removed_edges),
                "impact_summary": (
                    f"整改口径相对原报告口径：新增 {len(added_paths)} 条路径、"
                    f"{len(added_edge_ids)} 条关系；消失 {len(removed_paths)} 条路径、"
                    f"{len(removed_edge_ids)} 条关系"
                ),
            },
            "verification": {
                "consumed_record_ids": sorted(report_edges),
                "report_reproducible_record_ids": reproduced,
                "remediation_record_ids": sorted(remediation_edges),
                "consistent": consistent,
                "as_of_labels": {
                    "base": f"{MODE_LABELS['report']}@{query['as_of']}",
                    "target": f"{MODE_LABELS['remediation']}@{today}",
                },
                "note": (
                    "比对基准消费的血缘记录与原报告可复现路径一致"
                    if consistent
                    else "比对基准消费的血缘记录与原报告可复现路径不一致"
                ),
            },
        }

    def _verify_report_reproduction(
        self, *, report: dict[str, Any], as_of: str, max_depth: int
    ) -> tuple[bool, list[int]]:
        """独立重跑一次报告复现，核对比对基准消费的记录确实来自这些路径。"""
        reproduced = self.find_paths(
            start=report["start"],
            end=report["end"],
            as_of=_parse_date(as_of) or date.today(),
            mode="report",
            max_depth=max_depth,
        )
        reproduced_ids = sorted(
            {rid for path in reproduced["paths"] for rid in path["record_ids"]}
        )
        consumed = sorted(report["record_ids"])
        return reproduced_ids == consumed, reproduced_ids

    def _find_edge_payload(self, record_id: int, as_of: str) -> dict[str, Any]:
        row = next((row for row in self._all_rows() if _record_id(row) == record_id), None)
        if row is None:
            return {"记录ID": record_id, "链路编号": "", "上游实体": "", "下游实体": ""}
        return self._step_payload(row, _parse_date(as_of) or date.today())

    def _create_compare_task(
        self,
        *,
        query: dict[str, Any],
        added_edges: list[dict[str, Any]],
        removed_edges: list[dict[str, Any]],
        added_count: int,
        removed_count: int,
    ) -> dict[str, Any]:
        rows = store.rows("graph_compare")
        next_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        report = query["modes"]["report"]
        remediation = query["modes"]["remediation"]
        today = _iso(date.today())
        task: dict[str, Any] = {
            "id": next_id,
            "status": "待执行",
            "pending": True,
            "abnormal": bool(added_count or removed_count),
            "任务编号": f"GRAP-LIN-{next_id:04d}",
            "基准图": f"{query['start']}→{query['end']} {MODE_LABELS['report']}@{query['as_of']}",
            "对比图": f"{query['start']}→{query['end']} {MODE_LABELS['remediation']}@{today}",
            "比对维度": "versioned-lineage-diff",
            "新增实体": added_count,
            "删除实体": removed_count,
            "变更关系": added_count + removed_count,
            "比对时间": today,
            "对照请求日期": query["as_of"],
            "基准取数时点": query["as_of"],
            "对比取数时点": today,
            "基准模式": "report",
            "对比模式": "remediation",
            "基准记录ID": ",".join(map(str, report["record_ids"])),
            "对比记录ID": ",".join(map(str, remediation["record_ids"])),
            "新增记录ID": ",".join(str(edge["记录ID"]) for edge in added_edges),
            "删除记录ID": ",".join(str(edge["记录ID"]) for edge in removed_edges),
            "影响摘要": (
                f"在 {query['as_of']} 的报告口径上按 {today} 的已知事实核对："
                f"新增关系 {added_count} 条，消失关系 {removed_count} 条"
            ),
        }
        rows.append(task)
        return task

    def compare_tasks(self) -> list[dict[str, Any]]:
        return [
            row
            for row in store.rows("graph_compare")
            if str(row.get("比对维度", "")).startswith("versioned-lineage")
        ]

    def query_log(self) -> list[dict[str, Any]]:
        return list(self._query_log.values())

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["cross_module_consumers"] = ["graph_evolve", "graph_compare"]
        base["versioned_query"] = {
            "modes": list(VALID_MODES),
            "query_count": len(self._query_log),
            "compare_task_count": len(self.compare_tasks()),
        }
        return base


def _visible_valid_only(row: dict[str, Any], as_of: date) -> bool:
    if _valid_from(row) > as_of:
        return True
    valid_to = _valid_to(row)
    return valid_to is not None and as_of >= valid_to


def _edge_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _normalize(row.get("上游实体")),
        _normalize(row.get("下游实体")),
        _iso(_valid_from(row)),
        _iso(_recorded_at(row)),
        _record_id(row),
    )
