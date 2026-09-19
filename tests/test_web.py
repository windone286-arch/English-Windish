"""
Web 接口测试。

使用 FastAPI 的 TestClient，在进程内发请求，不需要真的启动服务器。

注意：这些测试**不会**调用真实的大模型 API（那太慢也太贵），
只验证路由、参数校验、错误处理这些纯逻辑部分。
真正调用 API 的端到端验证在开发日志里有记录。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

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
        assert "stages" in data

    def test_health_status_reflects_config(self):
        """配置完整时 status 为 ok，否则为 config_error。"""
        data = client.get("/api/health").json()
        assert data["status"] in ("ok", "config_error")

        if data["problems"]:
            assert data["status"] == "config_error"
        else:
            assert data["status"] == "ok"

    def test_health_lists_stages(self):
        data = client.get("/api/health").json()
        assert len(data["stages"]) == 4
        assert data["stages"][0] == "识别图片文字"


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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
