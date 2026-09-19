"""
历史记录存储测试。

用临时数据库，不污染真实数据。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage import (  # noqa: E402
    KIND_IMAGE,
    KIND_TEXT,
    HistoryStore,
    _make_preview,
    _make_summary,
)


@pytest.fixture()
def store(tmp_path):
    """每个测试用独立数据库。"""
    return HistoryStore(db_path=tmp_path / "history_test.db")


def _sample_result(text: str = "The book is interesting.") -> dict:
    return {
        "created_at": "2026-09-19 12:00:00",
        "raw_text": text,
        "sentences": [{"index": 1, "original": text}],
        "words": [{"word": "interesting"}],
        "stats": {"sentence_count": 1, "grammar_count": 2, "word_count": 1},
    }


# ======================================================================
# 摘要与预览
# ======================================================================


class TestSummaryHelpers:
    def test_summary_from_stats(self):
        result = _sample_result()
        assert _make_summary(result) == "识别 1 句 | 语法点 2 处 | 生词 1 个"

    def test_summary_falls_back_to_lengths(self):
        """没有 stats 时用列表长度兜底。"""
        result = {"sentences": [{}, {}, {}], "words": [{}]}
        assert "识别 3 句" in _make_summary(result)
        assert "生词 1 个" in _make_summary(result)

    def test_summary_when_empty(self):
        assert _make_summary({}) == "未解析出内容"

    def test_preview_truncates_long_text(self):
        long_text = "word " * 100
        preview = _make_preview(long_text, length=20)
        assert preview.endswith("…")
        assert len(preview) == 21  # 20 字符 + 省略号

    def test_preview_keeps_short_text(self):
        assert _make_preview("short text") == "short text"

    def test_preview_collapses_whitespace(self):
        """换行和多余空格应该被压平，避免列表里排版错乱。"""
        assert _make_preview("line one\n\n  line two") == "line one line two"

    def test_preview_handles_empty(self):
        assert _make_preview("") == ""


# ======================================================================
# 增删查
# ======================================================================


class TestHistoryStore:
    def test_save_returns_id(self, store):
        rid = store.save(KIND_TEXT, "hello", _sample_result())
        assert isinstance(rid, int)
        assert rid > 0

    def test_get_round_trip(self, store):
        rid = store.save(KIND_TEXT, "hello world", _sample_result("hello world"))

        record = store.get(rid)
        assert record is not None
        assert record["kind"] == KIND_TEXT
        assert record["source"] == "hello world"
        assert record["result"]["raw_text"] == "hello world"

    def test_get_missing_returns_none(self, store):
        assert store.get(99999) is None

    def test_list_is_newest_first(self, store):
        store.save(KIND_TEXT, "first", _sample_result())
        store.save(KIND_TEXT, "second", _sample_result())
        store.save(KIND_IMAGE, "third.jpg", _sample_result())

        items = store.list()
        assert len(items) == 3
        assert items[0].source == "third.jpg"
        assert items[-1].source == "first"

    def test_list_excludes_payload(self, store):
        """列表不应该带上完整结果，否则记录多了会很慢。"""
        store.save(KIND_TEXT, "x", _sample_result())
        item = store.list()[0]

        payload = item.to_dict()
        assert "result" not in payload
        assert "payload" not in payload
        assert set(payload) == {"id", "kind", "source", "created_at", "summary", "preview"}

    def test_list_pagination(self, store):
        for i in range(10):
            store.save(KIND_TEXT, f"item {i}", _sample_result())

        page1 = store.list(limit=4, offset=0)
        page2 = store.list(limit=4, offset=4)

        assert len(page1) == 4
        assert len(page2) == 4
        assert page1[0].id != page2[0].id

    def test_count(self, store):
        assert store.count() == 0
        store.save(KIND_TEXT, "a", _sample_result())
        store.save(KIND_TEXT, "b", _sample_result())
        assert store.count() == 2

    def test_delete(self, store):
        rid = store.save(KIND_TEXT, "to delete", _sample_result())

        assert store.delete(rid) is True
        assert store.get(rid) is None
        assert store.count() == 0

    def test_delete_missing_returns_false(self, store):
        assert store.delete(99999) is False

    def test_clear(self, store):
        for i in range(5):
            store.save(KIND_TEXT, f"item {i}", _sample_result())

        assert store.clear() == 5
        assert store.count() == 0

    def test_both_kinds_stored(self, store):
        store.save(KIND_IMAGE, "photo.jpg", _sample_result())
        store.save(KIND_TEXT, "some text", _sample_result())

        kinds = {item.kind for item in store.list()}
        assert kinds == {KIND_IMAGE, KIND_TEXT}

    def test_persistence_across_instances(self, tmp_path):
        """同一个数据库文件，换一个实例应该还能读到。"""
        db = tmp_path / "shared.db"

        store1 = HistoryStore(db_path=db)
        rid = store1.save(KIND_TEXT, "persisted", _sample_result())

        store2 = HistoryStore(db_path=db)
        record = store2.get(rid)
        assert record is not None
        assert record["source"] == "persisted"

    def test_long_source_truncated(self, store):
        """来源字段过长时截断，避免列表排版被撑破。"""
        rid = store.save(KIND_TEXT, "x" * 500, _sample_result())
        assert len(store.get(rid)["source"]) == 200

    def test_chinese_content_survives(self, store):
        """中文内容不能被编码问题破坏。"""
        result = _sample_result("这是一个中文测试。")
        rid = store.save(KIND_TEXT, "中文来源", result)

        record = store.get(rid)
        assert record["source"] == "中文来源"
        assert record["result"]["raw_text"] == "这是一个中文测试。"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
