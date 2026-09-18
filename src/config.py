"""
配置管理模块。

职责：
1. 从 .env 文件读取 API Key 等敏感配置
2. 提供统一的配置访问接口
3. 校验必要配置是否存在

设计原则：
- 密钥只从环境变量读取，绝不硬编码
- 缺失配置时给出明确的中文错误提示，而不是让程序抛一个看不懂的异常
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（本文件位于 src/config.py，所以上两级是根目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    """加载 .env 文件。若不存在则静默跳过（依赖真实的系统环境变量）。"""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)


@dataclass
class Config:
    """应用配置。

    所有配置项集中在此，避免散落在各处直接调 os.getenv。
    """

    # ---- 模型服务商 ----
    # 支持「视觉」与「文本」使用不同的服务商。
    # 原因：DeepSeek 推理强但**没有视觉模型**，所以图片识别必须用别家。
    # 这是本项目的一个重要设计决策，详见 docs/devlog.md
    vision_provider: str = "dashscope"
    text_provider: str = "deepseek"

    # ---- API Keys ----
    dashscope_api_key: str = ""
    zhipu_api_key: str = ""
    deepseek_api_key: str = ""

    # ---- 模型名称 ----
    vision_model: str = "qwen-vl-max"
    text_model: str = "deepseek-chat"

    # ---- 自定义 API 地址（用于第三方中转站）----
    # 留空则使用代码内置的官方地址
    deepseek_base_url: str = ""

    # ---- 运行配置 ----
    debug: bool = True

    # ---- 项目路径 ----
    project_root: Path = field(default=PROJECT_ROOT)

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def sample_dir(self) -> Path:
        return self.data_dir / "samples"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def prompt_dir(self) -> Path:
        return self.project_root / "src" / "prompts"

    def get_api_key(self, provider: str | None = None) -> str:
        """获取指定服务商的 API Key。

        Args:
            provider: 服务商名称。默认使用 text_provider

        Returns:
            API Key 字符串

        Raises:
            ValueError: 服务商不支持，或 Key 未配置
        """
        provider = provider or self.text_provider

        key_map = {
            "dashscope": (self.dashscope_api_key, "DASHSCOPE_API_KEY", "阿里通义千问"),
            "zhipu": (self.zhipu_api_key, "ZHIPU_API_KEY", "智谱 GLM"),
            "deepseek": (self.deepseek_api_key, "DEEPSEEK_API_KEY", "DeepSeek"),
        }

        if provider not in key_map:
            supported = "、".join(key_map.keys())
            raise ValueError(f"不支持的服务商：{provider}。当前支持：{supported}")

        key, env_name, display_name = key_map[provider]

        if not key:
            raise ValueError(
                f"未配置 {display_name} 的 API Key。\n"
                f"请按以下步骤操作：\n"
                f"  1. 复制项目根目录的 .env.example 为 .env\n"
                f"  2. 在 .env 中填入 {env_name}=你的密钥\n"
                f"  3. 注意：.env 不会被提交到 Git，可安全存放密钥"
            )

        return key

    def validate(self) -> list[str]:
        """校验配置完整性，返回问题列表（空列表表示没问题）。

        会分别检查视觉和文本两个环节的 Key 是否就位，
        因为本项目允许两者使用不同的服务商。
        """
        problems: list[str] = []

        for role, provider in (
            ("视觉（图片识别）", self.vision_provider),
            ("文本（语法分析/单词精讲）", self.text_provider),
        ):
            try:
                self.get_api_key(provider)
            except ValueError as exc:
                problems.append(f"【{role}】{exc}")

        if self.vision_provider == "deepseek":
            problems.append(
                "【配置错误】DeepSeek 没有视觉模型，无法用于图片识别。\n"
                "  请把 VISION_PROVIDER 改为 dashscope 或 zhipu。"
            )

        return problems

    @classmethod
    def from_env(cls) -> "Config":
        """从环境变量创建配置对象。"""
        load_env()

        debug_raw = os.getenv("DEBUG", "true").strip().lower()

        # 视觉服务商默认 dashscope；若用户只配了智谱，则自动切到智谱
        default_vision = (
            "dashscope" if os.getenv("DASHSCOPE_API_KEY") else (
                "zhipu" if os.getenv("ZHIPU_API_KEY") else "dashscope"
            )
        )

        vision_provider = os.getenv("VISION_PROVIDER", default_vision).strip()

        # 视觉模型名与服务商匹配（用户没显式指定时）
        default_vision_model = {
            "dashscope": "qwen-vl-max",
            "zhipu": "glm-4v-plus",
        }.get(vision_provider, "qwen-vl-max")

        return cls(
            vision_provider=vision_provider,
            text_provider=os.getenv("TEXT_PROVIDER", "deepseek").strip(),
            dashscope_api_key=os.getenv("DASHSCOPE_API_KEY", "").strip(),
            zhipu_api_key=os.getenv("ZHIPU_API_KEY", "").strip(),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            vision_model=os.getenv("VISION_MODEL", default_vision_model).strip(),
            text_model=os.getenv("TEXT_MODEL", "deepseek-chat").strip(),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "").strip(),
            debug=debug_raw in ("true", "1", "yes", "on"),
        )


# 全局单例。使用 get_config() 获取，避免重复解析 .env。
_config: Config | None = None


def get_config(reload: bool = False) -> Config:
    """获取全局配置实例。

    Args:
        reload: 是否强制重新加载（改了 .env 后用）
    """
    global _config
    if _config is None or reload:
        _config = Config.from_env()
    return _config
