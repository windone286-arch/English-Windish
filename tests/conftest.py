"""
pytest 全局配置。

## 为什么需要这个文件

`.env` 里配了 `ACCESS_PASSWORD`（部署必需），而 `config.py` 在导入时
会把 `.env` 读进 `os.environ`。于是所有测试请求都被当成未登录处理——
页面被重定向到登录页，API 返回 401，整个测试套件全线失败。

这个坑很隐蔽：本地 `.env` 没配密码时测试全绿，一部署就变红，
而且报错信息看起来像是「页面结构不对」，完全没有指向真正的原因。

## 解决办法

用 autouse fixture 在每个测试执行前清掉这两个变量。

为什么要用 fixture 而不是在模块顶层直接 pop？因为模块顶层的代码在
**导入阶段**执行，而 dotenv 的加载发生在 `src.web.app` 被导入时，
顺序无法保证。fixture 在**测试执行阶段**运行，此时所有导入都已完成，
清理一定是有效的。

需要测试鉴权本身的用例（test_auth.py）在自己的 fixture 里重新设置这两个变量，
autouse fixture 与它们不冲突。
"""

from __future__ import annotations

import os

import pytest

# 会干扰测试的部署期变量
_DEPLOY_VARS = ("ACCESS_PASSWORD", "DAILY_LIMIT")


@pytest.fixture(autouse=True)
def _disable_access_protection():
    """每个测试前关闭访问保护，测试后不恢复。

    不恢复是有意的：恢复会让「某个测试设了密码」的副作用泄漏到
    后续测试，导致失败顺序相关、难以复现。
    """
    for name in _DEPLOY_VARS:
        os.environ.pop(name, None)
    yield
