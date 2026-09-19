"""
分析历史存储。

用 SQLite 保存每一次分析的结果，支持回看、删除。

设计考虑：

**为什么用 SQLite 而不是 JSON 文件？**
1. 单文件、零配置，不需要额外服务
2. 支持按时间倒序分页查询，不用把所有记录读进内存
3. 单条记录的读取是 O(log n)，记录变多也不会变慢

**为什么列表查询不返回完整内容？**
一次分析的完整 JSON 可能几十 KB，列表里返回几十条就是几 MB。
所以 list() 只返回摘要字段，详情按需加载。

**线程安全**：SQLite 连接不能跨线程复用，所以每次操作新建连接。
Python 的 sqlite3 默认 clone 开销很小，这里不构成性能问题。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# 记录类型
KIND_IMAGE = "image"
KIND_TEXT = "text"


@dataclass
class HistoryItem:
    """历史记录摘要（不含完整结果）。"""

    id: int
    kind: str
    """image 或 text"""

    source: str
    """图片来源的文件名，或文本内容的开头片段"""

    created_at: str
    summary: str
    """一行摘要，如「识别 5 句 | 语法点 8 处 | 生词 6 个」"""

    preview: str
    """原文开头 60 字，用于列表里快速辨认"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "created_at": self.created_at,
            "summary": self.summary,
            "preview": self.preview,
        }


class HistoryStore:
    """分析历史存储。"""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else self._default_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @staticmethod
    def _default_path() -> Path:
        from src.config import get_config

        return get_config().data_dir / "history.db"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind       TEXT    NOT NULL,
                    source     TEXT    NOT NULL,
                    created_at TEXT    NOT NULL,
                    summary    TEXT    NOT NULL DEFAULT '',
                    preview    TEXT    NOT NULL DEFAULT '',
                    payload    TEXT    NOT NULL
                )
                """
            )
            # 按时间倒序查询是最高频操作，建索引
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_analyses_created "
                "ON analyses(created_at DESC)"
            )
            # 每日用量计数表。
            #
            # 这张表存的不是业务数据，而是**成本账本**：每天一行，
            # 记录当天已经消耗了多少次分析额度。有了它才能在超限时
            # 及时拦住请求，而不是等月底看到账单才发现。
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage (
                    day   TEXT    PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.commit()

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def save(
        self,
        kind: str,
        source: str,
        result_dict: dict[str, Any],
    ) -> int:
        """保存一次分析结果，返回记录 id。"""
        summary = _make_summary(result_dict)
        preview = _make_preview(result_dict.get("raw_text", ""))
        created_at = result_dict.get("created_at") or datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO analyses (kind, source, created_at, summary, preview, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    kind,
                    source[:200],
                    created_at,
                    summary,
                    preview,
                    json.dumps(result_dict, ensure_ascii=False),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def list(self, limit: int = 50, offset: int = 0) -> list[HistoryItem]:
        """列出历史记录（按时间倒序，不含完整内容）。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, kind, source, created_at, summary, preview
                FROM analyses
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()

        return [
            HistoryItem(
                id=row["id"],
                kind=row["kind"],
                source=row["source"],
                created_at=row["created_at"],
                summary=row["summary"],
                preview=row["preview"],
            )
            for row in rows
        ]

    def get(self, record_id: int) -> dict[str, Any] | None:
        """读取单条记录的完整内容。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, kind, source, created_at, summary, payload "
                "FROM analyses WHERE id = ?",
                (record_id,),
            ).fetchone()

        if not row:
            return None

        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            payload = {}

        return {
            "id": row["id"],
            "kind": row["kind"],
            "source": row["source"],
            "created_at": row["created_at"],
            "summary": row["summary"],
            "result": payload,
        }

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0])

    # ------------------------------------------------------------------
    # 删
    # ------------------------------------------------------------------

    def delete(self, record_id: int) -> bool:
        """删除单条记录。返回是否真的删掉了。"""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM analyses WHERE id = ?", (record_id,))
            conn.commit()
            return cursor.rowcount > 0

    def clear(self) -> int:
        """清空全部历史，返回删除条数。"""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
            conn.execute("DELETE FROM analyses")
            conn.commit()
        return int(total)

    # ------------------------------------------------------------------
    # 用量计数（成本控制，与历史记录无关）
    # ------------------------------------------------------------------

    def get_usage(self, day: str) -> int:
        """读取某一天已使用的分析次数。day 格式为 YYYY-MM-DD。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT count FROM usage WHERE day = ?", (day,)
            ).fetchone()
        return int(row["count"]) if row else 0

    def bump_usage(self, day: str) -> int:
        """把某一天的计数加一，返回加完后的值。

        用一条 UPSERT 完成「没有就插入 1，已存在就加 1」，
        而不是先查后写两次往返——少一次往返，也少一个并发窗口。
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO usage(day, count) VALUES (?, 1)
                ON CONFLICT(day) DO UPDATE SET count = count + 1
                """,
                (day,),
            )
            conn.commit()
            row = conn.execute(
                "SELECT count FROM usage WHERE day = ?", (day,)
            ).fetchone()
        return int(row["count"]) if row else 0


def _make_summary(result: dict[str, Any]) -> str:
    """生成一行摘要。"""
    stats = result.get("stats") or {}
    sentences = stats.get("sentence_count", len(result.get("sentences") or []))
    grammar = stats.get("grammar_count", 0)
    words = stats.get("word_count", len(result.get("words") or []))

    if not sentences and not words:
        return "未解析出内容"

    return f"识别 {sentences} 句 | 语法点 {grammar} 处 | 生词 {words} 个"


def _make_preview(text: str, length: int = 60) -> str:
    """截取原文开头，用于列表辨认。"""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= length:
        return cleaned
    return cleaned[:length] + "…"


# 全局单例
_store: HistoryStore | None = None


def get_history_store() -> HistoryStore:
    global _store
    if _store is None:
        _store = HistoryStore()
    return _store
