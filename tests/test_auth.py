"""
访问保护与用量限额测试。

这两件事都关乎「花钱」：密码拦住陌生人，限额兜住最坏情况。
所以测试要覆盖的不仅是正常路径，更重要的是**绕过尝试**——
伪造的 cookie、篡改的签名、过期的凭证，都必须被拒绝。

测试不依赖真实 API，也不依赖真实的 .env 配置，
全部通过 monkeypatch 环境变量切换。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.web import auth  # noqa: E402
from src.web.app import app  # noqa: E402


PASSWORD = "test-password-123"


@pytest.fixture
def guarded(monkeypatch):
    """启用访问密码，并返回一个全新的客户端。

    必须新建 TestClient：模块级那个会保留其他测试留下的 cookie，
    导致「未登录」的断言莫名其妙通过或失败。
    """
    monkeypatch.setenv("ACCESS_PASSWORD", PASSWORD)
    monkeypatch.delenv("DAILY_LIMIT", raising=False)
    return TestClient(app)


@pytest.fixture
def open_access(monkeypatch):
    """不设密码，模拟本地开发环境。"""
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    monkeypatch.delenv("DAILY_LIMIT", raising=False)
    return TestClient(app)


# ======================================================================
# 凭证签发与校验（纯函数，不经过 HTTP）
# ======================================================================


class TestToken:
    def test_roundtrip(self, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", PASSWORD)
        token = auth.issue_token()
        assert auth.verify_token(token)

    def test_rejects_expired(self, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", PASSWORD)
        expired = auth.issue_token(ttl_seconds=-1)
        assert not auth.verify_token(expired)

    def test_rejects_tampered_signature(self, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", PASSWORD)
        expires = str(int(time.time()) + 3600)
        assert not auth.verify_token(f"{expires}.{'0' * 64}")

    def test_rejects_tampered_expiry(self, monkeypatch):
        """把过期时间往后改，签名就对不上了。"""
        monkeypatch.setenv("ACCESS_PASSWORD", PASSWORD)
        token = auth.issue_token()
        _, _, signature = token.partition(".")
        forged = f"{int(time.time()) + 999999}.{signature}"
        assert not auth.verify_token(forged)

    def test_rejects_garbage(self, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", PASSWORD)
        for bad in (None, "", "abc", "abc.def", "12345"):
            assert not auth.verify_token(bad)

    def test_token_from_other_password_is_rejected(self, monkeypatch):
        """换了密码，旧凭证立刻失效。

        这正是「密钥由密码派生」带来的好处：不需要额外的 secret 管理，
        改密码就等同于吊销所有已发出的凭证。
        """
        monkeypatch.setenv("ACCESS_PASSWORD", PASSWORD)
        token = auth.issue_token()

        monkeypatch.setenv("ACCESS_PASSWORD", "another-password")
        assert not auth.verify_token(token)


class TestPasswordCheck:
    def test_disabled_means_always_pass(self, monkeypatch):
        monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
        assert not auth.is_enabled()
        assert auth.check_password("")
        assert auth.check_password("anything")

    def test_requires_exact_match(self, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", PASSWORD)
        assert auth.check_password(PASSWORD)
        assert not auth.check_password(PASSWORD + "x")
        assert not auth.check_password(PASSWORD.upper())
        assert not auth.check_password("")
        assert not auth.check_password(None)

    def test_surrounding_whitespace_is_tolerated(self, monkeypatch):
        """从聊天软件复制的密码常带空格，直接去掉而不是让用户自己找。"""
        monkeypatch.setenv("ACCESS_PASSWORD", PASSWORD)
        assert auth.check_password(f"  {PASSWORD}  ")


# ======================================================================
# 访问控制（HTTP 层）
# ======================================================================


class TestAccessControlDisabled:
    """未配置密码时，一切照常。"""

    def test_home_is_open(self, open_access):
        assert open_access.get("/").status_code == 200

    def test_api_is_open(self, open_access):
        assert open_access.get("/api/history").status_code == 200


class TestAccessControlEnabled:
    def test_home_redirects_to_login(self, guarded):
        resp = guarded.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"

    def test_api_returns_401(self, guarded):
        resp = guarded.get("/api/history")
        assert resp.status_code == 401
        assert resp.json()["type"] == "error"

    def test_login_page_is_reachable(self, guarded):
        """登录页自己必须能访问，否则陷入死循环。"""
        resp = guarded.get("/login")
        assert resp.status_code == 200
        assert "访问密码" in resp.text

    def test_static_assets_are_exempt(self, guarded):
        """静态资源要放行，否则登录页连样式都加载不出来。"""
        assert guarded.get("/static/style.css").status_code == 200
        assert guarded.get("/static/app.js").status_code == 200

    def test_wrong_password_is_rejected(self, guarded):
        resp = guarded.post(
            "/login", data={"password": "wrong"}, follow_redirects=False
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login?e=1"
        assert auth.COOKIE_NAME not in resp.cookies

    def test_correct_password_grants_access(self, guarded):
        resp = guarded.post(
            "/login", data={"password": PASSWORD}, follow_redirects=False
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"
        assert auth.COOKIE_NAME in resp.cookies

        # 拿到凭证后，首页和 API 都应放行
        assert guarded.get("/").status_code == 200
        assert guarded.get("/api/history").status_code == 200

    def test_forged_cookie_is_rejected(self, guarded):
        """手工塞一个 cookie 绕不过去。"""
        guarded.cookies.set(auth.COOKIE_NAME, "9999999999.deadbeef")
        assert guarded.get("/api/history").status_code == 401

    def test_logout_clears_cookie(self, guarded):
        guarded.post("/login", data={"password": PASSWORD}, follow_redirects=False)
        assert guarded.get("/api/history").status_code == 200

        guarded.get("/logout", follow_redirects=False)
        assert guarded.get("/api/history").status_code == 401


# ======================================================================
# 每日用量限额
# ======================================================================


def _stub_pipeline(monkeypatch, tmp_path):
    """把存储和流水线都换成替身，避免真实 API 与真实数据库。"""
    from src.storage import HistoryStore

    store = HistoryStore(db_path=tmp_path / "usage_test.db")
    monkeypatch.setattr("src.web.app.get_history_store", lambda: store)
    monkeypatch.setattr("src.web.app.analyze_text", _fake_analyze_text)
    return store


def _fake_analyze_text(text, word_count=8, no_words=False, on_progress=None):
    from src.models import AnalysisResult

    result = AnalysisResult(created_at="2026-09-19 13:00:00", raw_text=text)
    result.stats = {
        "sentence_count": 1,
        "grammar_count": 0,
        "grammar_types": {},
        "word_count": 1,
        "collocation_count": 0,
        "text_length": len(text),
    }
    return result


class TestDailyLimit:
    def test_not_limited_by_default(self, monkeypatch, tmp_path):
        """不设 DAILY_LIMIT 时不做限制——本地开发不该被拦住。"""
        monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
        monkeypatch.delenv("DAILY_LIMIT", raising=False)
        client = TestClient(app)
        _stub_pipeline(monkeypatch, tmp_path)

        for _ in range(3):
            resp = client.post("/api/analyze-text", data={"text": "hello"})
            assert resp.status_code == 200

    def test_rejects_after_limit_reached(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
        monkeypatch.setenv("DAILY_LIMIT", "2")
        client = TestClient(app)
        store = _stub_pipeline(monkeypatch, tmp_path)

        assert client.post("/api/analyze-text", data={"text": "a"}).status_code == 200
        assert client.post("/api/analyze-text", data={"text": "b"}).status_code == 200

        resp = client.post("/api/analyze-text", data={"text": "c"})
        assert resp.status_code == 429
        assert "已用完" in resp.json()["message"]

        # 被拒绝的请求不应计入用量，否则第二天会莫名其妙少一次额度
        today = _today()
        assert store.get_usage(today) == 2

    def test_invalid_limit_falls_back_to_unlimited(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
        monkeypatch.setenv("DAILY_LIMIT", "abc")
        client = TestClient(app)
        _stub_pipeline(monkeypatch, tmp_path)

        resp = client.post("/api/analyze-text", data={"text": "hello"})
        assert resp.status_code == 200

    def test_usage_is_counted_per_day(self, monkeypatch, tmp_path):
        """计数按天分桶，跨天自动重置。"""
        monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
        store = _stub_pipeline(monkeypatch, tmp_path)

        assert store.get_usage("2026-09-19") == 0
        store.bump_usage("2026-09-19")
        store.bump_usage("2026-09-19")
        assert store.get_usage("2026-09-19") == 2

        # 换一天，计数从 0 开始
        assert store.get_usage("2026-09-20") == 0


def _today() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d")
