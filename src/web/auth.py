"""
访问密码保护。

## 为什么需要这道门

项目部署到公网后，任何人拿到链接就能打开。而这个应用的每一次分析
都在消耗**真实付费**的 API 额度（图片识别走通义、文本分析走 DeepSeek）。
没有门，就等于把自己的钱包挂在公网上。

## 设计取舍

**1. 用环境变量开关，不写死在代码里**

只有设置了 `ACCESS_PASSWORD` 才启用鉴权。本地开发不设置，完全不受影响；
部署时配一个，门就立起来了。同一份代码适应两种场景，不需要维护两套分支。

**2. cookie 里存签名，不存密码**

如果把密码本身放进 cookie，一旦 cookie 泄露，密码也跟着泄露，
而且用户改密码就得让所有已登录会话全部失效。
这里改成：cookie 只放「过期时间 + 对该时间的签名」。
服务端能验证真伪，但 cookie 里不包含任何秘密。

**3. 无状态设计**

签名密钥由密码派生，不额外维护 secret；验证过程不查内存也不查数据库。
所以服务重启后已登录的会话依然有效——这一点对免费托管平台很重要，
它们会不定时休眠和重启，有状态的会话会被清空。

**4. 常量时间比较**

密码比对用 `hmac.compare_digest` 而不是 `==`。
`==` 在遇到第一个不同的字符时就返回，攻击者能通过响应时间的微小差异
逐字节猜出密码；常量时间比较消除了这个信道。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

# cookie 名称
COOKIE_NAME = "ew_access"

# 登录有效期，默认 30 天。
# 不设太长是因为 cookie 一旦泄露，有效期就是攻击者的可用窗口；
# 不设太短是因为这个站点是给面试官看的，三天两头要求重新登录很烦人。
DEFAULT_TTL_SECONDS = 30 * 24 * 3600

# 密钥派生时用的命名空间前缀。
# 作用是避免「同一个密码在不同项目里派生出相同的密钥」。
_NAMESPACE = "english-windish::v1"


def get_password() -> str:
    """读取配置的访问密码，未配置则返回空字符串。"""
    return (os.getenv("ACCESS_PASSWORD") or "").strip()


def is_enabled() -> bool:
    """是否启用了访问保护。

    每次调用都重新读环境变量，而不是在模块加载时算一次——
    这样测试可以随时切换，也让「改了配置需要重启」这件事变得明确。
    """
    return bool(get_password())


def _signing_key() -> bytes:
    """由密码派生签名密钥。

    用 SHA-256 而不是直接拿密码当密钥，是为了把任意长度的口令
    规整成固定长度的密钥，同时避免口令出现在任何存储或日志里。
    """
    material = f"{_NAMESPACE}::{get_password()}".encode()
    return hashlib.sha256(material).digest()


def check_password(candidate: str | None) -> bool:
    """校验用户提交的密码。未启用保护时一律放行。"""
    real = get_password()
    if not real:
        return True
    return hmac.compare_digest((candidate or "").strip(), real)


def _sign(expires: int) -> str:
    return hmac.new(
        _signing_key(), str(expires).encode(), hashlib.sha256
    ).hexdigest()


def issue_token(ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """签发登录凭证，格式为 `<过期时间戳>.<签名>`。"""
    expires = int(time.time()) + ttl_seconds
    return f"{expires}.{_sign(expires)}"


def verify_token(token: str | None) -> bool:
    """校验登录凭证。未启用保护时一律放行。"""
    if not is_enabled():
        return True

    if not token or "." not in token:
        return False

    expires_part, _, signature = token.partition(".")

    try:
        expires = int(expires_part)
    except ValueError:
        return False

    # 先验签名，再看过期——签名不对就没必要继续判断
    if not hmac.compare_digest(signature, _sign(expires)):
        return False

    return time.time() < expires


def is_https(headers_proto: str | None, scheme: str) -> bool:
    """判断当前请求是否走 HTTPS。

    用来决定 cookie 的 secure 属性。不能只看 scheme：
    部署在反向代理后面时，应用看到的是 http，而对外是 https，
    真实协议在 X-Forwarded-Proto 头里。

    判断错两个方向的后果不对称：
    - 该 True 却给了 False：cookie 可能在 http 上被明文发送（轻微风险）
    - 该 False 却给了 True：浏览器拒绝保存 cookie，用户**根本登录不进去**
    所以只有在明确确认是 HTTPS 时才设 secure。
    """
    if headers_proto:
        return headers_proto.split(",")[0].strip().lower() == "https"
    return scheme == "https"
