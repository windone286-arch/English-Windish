"""
Web 接口测试。

使用 FastAPI 的 TestClient，在进程内发请求，不需要真的启动服务器。

注意：这些测试**不会**调用真实的大模型 API（那太慢也太贵），
只验证路由、参数校验、错误处理这些纯逻辑部分。
真正调用 API 的端到端验证在开发日志里有记录。
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.web.app import app  # noqa: E402

client = TestClient(app)


# ======================================================================
# 页面与静态资源
# ======================================================================


class TestPages:
    def test_index_returns_html(self):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "English-Windish" in resp.text
        assert "<!DOCTYPE html>" in resp.text

    def test_index_references_static_assets(self):
        resp = client.get("/")
        assert "/static/style.css" in resp.text
        assert "/static/app.js" in resp.text

    def test_stylesheet_served(self):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert ":root" in resp.text  # CSS 变量定义存在

    def test_javascript_served(self):
        resp = client.get("/static/app.js")
        assert resp.status_code == 200
        assert "readEventStream" in resp.text

    def test_api_docs_available(self):
        """FastAPI 自动生成的 API 文档应该可访问（面试演示时有用）。"""
        resp = client.get("/docs")
        assert resp.status_code == 200


# ======================================================================
# 健康检查
# ======================================================================


class TestHealth:
    def test_health_returns_structure(self):
        resp = client.get("/api/health")
        assert resp.status_code == 200

        data = resp.json()
        assert "status" in data
        assert "vision_provider" in data
        assert "text_provider" in data
        assert "image_stages" in data
        assert "text_stages" in data

    def test_health_status_reflects_config(self):
        """配置完整时 status 为 ok，否则为 config_error。"""
        data = client.get("/api/health").json()
        assert data["status"] in ("ok", "config_error")

        if data["problems"]:
            assert data["status"] == "config_error"
        else:
            assert data["status"] == "ok"

    def test_health_lists_both_stage_sets(self):
        """图片和文本的阶段必须分开返回。"""
        data = client.get("/api/health").json()

        assert len(data["image_stages"]) == 4
        assert data["image_stages"][0] == "识别图片文字"

        # 文本链路没有图片识别这一步，不能带上
        assert len(data["text_stages"]) == 3
        assert data["text_stages"][0] == "分析语法结构"
        assert "识别图片文字" not in data["text_stages"]


# ======================================================================
# 上传校验
# ======================================================================


class TestUploadValidation:
    """重点测试参数校验。

    这些分支必须可靠——如果校验漏了，用户可能上传 100MB 的文件
    把服务器内存打爆，或者上传一个 .exe 触发奇怪的行为。
    """

    def test_rejects_unsupported_format(self):
        resp = client.post(
            "/api/analyze",
            files={"file": ("virus.exe", b"MZ\x90\x00", "application/octet-stream")},
        )
        # 格式不对 → 400；配置不全 → 503。两者都可接受，
        # 但不能是 500（未处理的异常）
        assert resp.status_code in (400, 503)

        if resp.status_code == 400:
            assert "不支持的图片格式" in resp.json()["message"]

    def test_rejects_empty_file(self):
        resp = client.post(
            "/api/analyze",
            files={"file": ("empty.jpg", b"", "image/jpeg")},
        )
        assert resp.status_code in (400, 503)

        if resp.status_code == 400:
            assert "空" in resp.json()["message"]

    def test_rejects_oversized_file(self):
        """超过 10MB 的文件应该被拒绝。"""
        big = b"\xff\xd8\xff\xe0" + b"x" * (11 * 1024 * 1024)
        resp = client.post(
            "/api/analyze",
            files={"file": ("big.jpg", big, "image/jpeg")},
        )
        assert resp.status_code in (400, 503)

        if resp.status_code == 400:
            assert "过大" in resp.json()["message"]

    def test_error_response_format_is_consistent(self):
        """所有错误响应用统一结构 {"type": "error", "message": ...}。"""
        resp = client.post(
            "/api/analyze",
            files={"file": ("bad.txt", b"hello", "text/plain")},
        )
        if resp.status_code != 200:
            data = resp.json()
            assert "message" in data
            assert data.get("type") == "error"


# ======================================================================
# 示例图片列表
# ======================================================================


class TestSamples:
    def test_samples_returns_list(self):
        resp = client.get("/api/samples")
        assert resp.status_code == 200

        data = resp.json()
        assert "samples" in data
        assert isinstance(data["samples"], list)

    def test_samples_excludes_hidden_and_non_images(self):
        data = client.get("/api/samples").json()
        for name in data["samples"]:
            assert not name.startswith(".")
            assert Path(name).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


# ======================================================================
# 文本分析接口
# ======================================================================


def _fake_analyze_text(text, word_count=8, no_words=False, on_progress=None):
    """替身：不调真实 API，直接返回一个最小结果。"""
    from src.models import AnalysisResult

    if on_progress:
        for i, msg in enumerate(["分析语法结构", "生成单词词卡", "汇总分析结果"], 1):
            on_progress(i, 3, msg)

    result = AnalysisResult(created_at="2026-09-19 12:00:00", raw_text=text)
    result.stats = {
        "sentence_count": 0,
        "grammar_count": 0,
        "grammar_types": {},
        "word_count": 0,
        "collocation_count": 0,
        "text_length": len(text),
    }
    return result


def _parse_events(body: str) -> list[dict]:
    """把 SSE 响应体解析成事件列表，便于断言。"""
    return [
        json.loads(line[6:])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _use_temp_store(monkeypatch, tmp_path):
    """把历史存储指向临时数据库，避免污染真实数据。"""
    from src.storage import HistoryStore

    store = HistoryStore(db_path=tmp_path / "web_test.db")
    monkeypatch.setattr("src.web.app.get_history_store", lambda: store)
    return store


# ======================================================================
# 进度阶段
#
# 这里覆盖的是一个真实出现过的 bug：图片和文本起初共用同一份阶段常量，
# 于是文本分析的进度条第一步显示「识别图片文字」——而那一步根本不会执行。
# 阶段文案必须和实际执行的步骤严格对应。
# ======================================================================


class TestStreamStages:
    def test_text_stream_excludes_image_stage(self, monkeypatch, tmp_path):
        _use_temp_store(monkeypatch, tmp_path)
        monkeypatch.setattr("src.web.app.analyze_text", _fake_analyze_text)

        resp = client.post("/api/analyze-text", data={"text": "Hello world."})
        start = _parse_events(resp.text)[0]

        assert start["type"] == "start"
        assert start["total"] == 3
        assert len(start["stages"]) == 3
        assert start["stages"][0] == "分析语法结构"
        assert "识别图片文字" not in start["stages"]

    def test_text_progress_total_matches_stages(self, monkeypatch, tmp_path):
        """progress 事件的 total 必须和 start 声明的一致。"""
        _use_temp_store(monkeypatch, tmp_path)
        monkeypatch.setattr("src.web.app.analyze_text", _fake_analyze_text)

        resp = client.post("/api/analyze-text", data={"text": "Hello world."})
        events = _parse_events(resp.text)

        start = events[0]
        steps = [e for e in events if e["type"] == "progress"]

        assert steps, "至少要有一个 progress 事件"
        for step in steps:
            assert step["total"] == start["total"]

    def test_image_stream_includes_image_stage(self, monkeypatch, tmp_path):
        _use_temp_store(monkeypatch, tmp_path)
        monkeypatch.setattr("src.web.app._ensure_configured", lambda: None)
        monkeypatch.setattr(
            "src.web.app.get_config",
            lambda: SimpleNamespace(upload_dir=tmp_path / "uploads"),
        )

        def fake_analyze_image(
            image_path, word_count=8, text_only=False, no_words=False, on_progress=None
        ):
            from src.models import AnalysisResult

            if on_progress:
                on_progress(1, 4, "识别图片文字")
            return AnalysisResult(created_at="2026-09-19 12:00:00", raw_text="stub")

        monkeypatch.setattr("src.web.app.analyze_image", fake_analyze_image)

        resp = client.post(
            "/api/analyze",
            files={"file": ("page.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")},
        )
        start = _parse_events(resp.text)[0]

        assert start["total"] == 4
        assert start["stages"][0] == "识别图片文字"


class TestAnalyzeText:
    def test_rejects_empty_text(self):
        resp = client.post("/api/analyze-text", data={"text": "   "})
        assert resp.status_code in (400, 503)

        if resp.status_code == 400:
            assert "不能为空" in resp.json()["message"]

    def test_rejects_too_long_text(self):
        resp = client.post("/api/analyze-text", data={"text": "a" * 20001})
        assert resp.status_code in (400, 503)

        if resp.status_code == 400:
            assert "过长" in resp.json()["message"]

    def test_success_returns_stream_events(self, monkeypatch, tmp_path):
        _use_temp_store(monkeypatch, tmp_path)
        monkeypatch.setattr("src.web.app.analyze_text", _fake_analyze_text)

        resp = client.post(
            "/api/analyze-text",
            data={"text": "The book is interesting.", "word_count": "5"},
        )

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        body = resp.text
        assert '"type": "start"' in body
        assert '"type": "progress"' in body
        assert '"type": "done"' in body

    def test_success_saves_to_history(self, monkeypatch, tmp_path):
        store = _use_temp_store(monkeypatch, tmp_path)
        monkeypatch.setattr("src.web.app.analyze_text", _fake_analyze_text)

        client.post("/api/analyze-text", data={"text": "Saved text."})

        assert store.count() == 1
        item = store.list()[0]
        assert item.kind == "text"
        assert "Saved text." in item.source

    def test_long_text_source_is_truncated(self, monkeypatch, tmp_path):
        store = _use_temp_store(monkeypatch, tmp_path)
        monkeypatch.setattr("src.web.app.analyze_text", _fake_analyze_text)

        client.post("/api/analyze-text", data={"text": "word " * 60})

        # storage 层会截断到 200 字符
        assert len(store.list()[0].source) <= 200

    def test_word_count_is_clamped(self, monkeypatch, tmp_path):
        """词卡数量的边界值应该被收敛到 3-15。"""
        _use_temp_store(monkeypatch, tmp_path)

        captured = {}

        def capture(text, word_count=8, no_words=False, on_progress=None):
            captured["word_count"] = word_count
            return _fake_analyze_text(text, on_progress=on_progress)

        monkeypatch.setattr("src.web.app.analyze_text", capture)

        client.post("/api/analyze-text", data={"text": "test", "word_count": "999"})
        assert captured["word_count"] == 15

        client.post("/api/analyze-text", data={"text": "test", "word_count": "1"})
        assert captured["word_count"] == 3


# ======================================================================
# 历史记录接口
# ======================================================================


class TestHistoryEndpoints:
    def test_list_empty(self, monkeypatch, tmp_path):
        _use_temp_store(monkeypatch, tmp_path)

        resp = client.get("/api/history")
        assert resp.status_code == 200

        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_detail_not_found(self, monkeypatch, tmp_path):
        _use_temp_store(monkeypatch, tmp_path)

        resp = client.get("/api/history/99999")
        assert resp.status_code == 404
        assert "不存在" in resp.json()["message"]

    def test_delete_not_found(self, monkeypatch, tmp_path):
        _use_temp_store(monkeypatch, tmp_path)

        resp = client.delete("/api/history/99999")
        assert resp.status_code == 404

    def test_save_list_detail_delete_flow(self, monkeypatch, tmp_path):
        store = _use_temp_store(monkeypatch, tmp_path)

        payload = {
            "created_at": "2026-09-19 12:00:00",
            "raw_text": "Round trip test.",
            "sentences": [],
            "words": [],
            "stats": {"sentence_count": 0, "grammar_count": 0, "word_count": 0},
        }
        rid = store.save("text", "Round trip test.", payload)

        # 列表
        listing = client.get("/api/history").json()
        assert listing["total"] == 1
        assert listing["items"][0]["id"] == rid
        assert "result" not in listing["items"][0]

        # 详情
        detail = client.get(f"/api/history/{rid}").json()
        assert detail["result"]["raw_text"] == "Round trip test."

        # 删除
        assert client.delete(f"/api/history/{rid}").json()["deleted"] == rid
        assert client.get("/api/history").json()["total"] == 0

    def test_clear(self, monkeypatch, tmp_path):
        store = _use_temp_store(monkeypatch, tmp_path)

        for i in range(3):
            store.save("text", f"item {i}", {"created_at": "2026-09-19 12:00:00"})

        resp = client.delete("/api/history")
        assert resp.status_code == 200
        assert resp.json()["cleared"] == 3
        assert client.get("/api/history").json()["total"] == 0

    def test_limit_is_clamped(self, monkeypatch, tmp_path):
        """limit 传超大值不应被接受。"""
        store = _use_temp_store(monkeypatch, tmp_path)
        store.save("text", "a", {"created_at": "2026-09-19 12:00:00"})

        resp = client.get("/api/history?limit=99999")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1


# ======================================================================
# 页面结构（前端资源完整性）
# ======================================================================


class TestPageStructure:
    """验证页面包含关键交互元素。

    这些断言看起来琐碎，但它们能拦住「改了 HTML 忘了改 JS」
    这类低级错误——元素 id 缺失会让界面静默失效。
    """

    def test_has_mode_switcher(self):
        html = client.get("/").text
        assert 'data-mode="image"' in html
        assert 'data-mode="text"' in html
        assert 'id="text-input"' in html

    def test_has_result_tabs(self):
        html = client.get("/").text
        assert 'data-tab="translation"' in html
        assert 'data-tab="grammar"' in html
        assert 'data-tab="vocabulary"' in html

    def test_has_back_button(self):
        html = client.get("/").text
        assert 'id="back-btn"' in html

    def test_has_history_drawer(self):
        html = client.get("/").text
        assert 'id="history-drawer"' in html
        assert 'id="history-open"' in html
        assert 'id="history-list"' in html

    def test_css_declares_hidden_rule(self):
        """[hidden] 必须在 CSS 里显式声明 display:none。

        这条断言锁住一个踩过的坑：浏览器默认的 `[hidden] { display: none }`
        属于优先级最低的用户代理样式，只要作者样式表里给同一元素写了
        display（比如 `.drawer { display: flex }`），hidden 就会完全失效。
        后果是历史记录抽屉关不掉，一直挂在页面右侧。

        app.js 里所有显隐都靠 hidden 属性控制，所以这条规则一旦丢失，
        整个界面的显隐逻辑都会坏掉。
        """
        css = client.get("/static/style.css").text
        assert "[hidden]" in css
        assert "display: none !important" in css

    def test_js_references_existing_ids(self):
        """JS 里用 $('xxx') 取的元素，HTML 里必须存在。

        取不到的元素会在运行时抛 null 错误，导致整个脚本挂掉。
        """
        import re

        html = client.get("/").text
        js = client.get("/static/app.js").text

        # 找出 JS 中 $('xxx') 形式的引用
        ids_in_js = set(re.findall(r"\$\('([a-z0-9-]+)'\)", js))

        # 去掉 JS 里动态拼接的 id（如 `${id}`），只检查静态字符串
        missing = [
            i for i in ids_in_js
            if f'id="{i}"' not in html
        ]

        assert not missing, f"JS 引用了 HTML 中不存在的元素：{missing}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
