"""
大模型调用统一封装层。

职责：
1. 屏蔽不同服务商（阿里通义 / 智谱 / DeepSeek）的 API 差异
2. 统一处理重试、错误、超时
3. 统一处理 JSON 输出的解析与修复

设计要点：
- **统一接口**：上层业务代码只调 `LLMClient.chat()`，不关心底层是谁家的 API
- **JSON 解析容错**：大模型经常在 JSON 外面裹一层 ```json 代码块，或者多写几句废话。
  本模块自动剥离这些杂质，必要时调模型自我修复。
- **失败重试**：网络抖动是常态，指数退避重试能显著提升稳定性。
"""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from src.config import Config, get_config

# 各服务商的 API 端点
ENDPOINTS = {
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "deepseek": "https://api.deepseek.com/chat/completions",
}

# 支持 response_format={"type": "json_object"} 的服务商
# DeepSeek 的 API 不接受这个参数，传了会报错
JSON_MODE_SUPPORTED = {"dashscope", "zhipu"}

# 各家支持视觉的模型前缀，用于提前拦截「用文本模型读图」这类配置错误
VISION_MODEL_PREFIXES = ("qwen-vl", "qwen2-vl", "qwen3-vl", "glm-4v", "gpt-4", "claude-")


class LLMError(Exception):
    """大模型调用异常。"""


class LLMClient:
    """大模型客户端。

    支持「视觉」与「文本」走不同的服务商——这是本项目的核心配置策略。
    例如：用通义千问的 qwen-vl-max 读图，用 DeepSeek 做语法分析。
    每次调用时指定 role，客户端会自动挑选对应的服务商、Key 和模型。
    """

    def __init__(self, config: Config | None = None, timeout: float = 120.0):
        self.config = config or get_config()
        self.timeout = timeout

    def _resolve(
        self, role: str, model: str | None
    ) -> tuple[str, str, str]:
        """根据角色解析出 (provider, api_key, model)。

        Args:
            role: "vision" 或 "text"
            model: 用户显式指定的模型名，为 None 时用配置默认值
        """
        if role == "vision":
            provider = self.config.vision_provider
            default_model = self.config.vision_model
        else:
            provider = self.config.text_provider
            default_model = self.config.text_model

        api_key = self.config.get_api_key(provider)
        return provider, api_key, (model or default_model)

    def _endpoint(self, provider: str) -> str:
        """获取 API 端点，支持第三方中转站自定义地址。"""
        if provider == "deepseek" and self.config.deepseek_base_url:
            base = self.config.deepseek_base_url.rstrip("/")
            # 允许用户只填到域名，自动补全路径
            if not base.endswith("/chat/completions"):
                base = f"{base}/chat/completions"
            return base

        url = ENDPOINTS.get(provider)
        if not url:
            raise LLMError(f"未知的服务商：{provider}")
        return url

    # ------------------------------------------------------------------
    # 底层调用
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.3,
        max_retries: int = 3,
        json_mode: bool = False,
        role: str = "text",
    ) -> str:
        """调用大模型，返回文本回复。

        Args:
            messages: OpenAI 格式的消息列表
            model: 模型名称，默认按 role 取配置
            temperature: 温度。分析类任务建议低温度（0.1-0.3）以保证稳定性
            max_retries: 最大重试次数
            json_mode: 是否要求模型输出 JSON（部分服务商支持 response_format）
            role: "vision" 或 "text"，决定使用哪个服务商

        Returns:
            模型返回的文本

        Raises:
            LLMError: 所有重试均失败
        """
        provider, api_key, model = self._resolve(role, model)
        url = self._endpoint(provider)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        if json_mode and provider in JSON_MODE_SUPPORTED:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(url, headers=headers, json=payload)

                if resp.status_code != 200:
                    # 提取服务端返回的错误信息，便于排查
                    detail = resp.text[:500]
                    raise LLMError(
                        f"API 返回错误 {resp.status_code}：{detail}"
                    )

                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return content

            except Exception as exc:  # noqa: BLE001 — 需要捕获所有可能的网络/解析异常
                last_error = exc
                if attempt < max_retries - 1:
                    # 指数退避：1s, 2s, 4s
                    wait = 2 ** attempt
                    if self.config.debug:
                        print(f"  [重试 {attempt + 1}/{max_retries}] {exc}，{wait}s 后重试...")
                    time.sleep(wait)

        raise LLMError(
            f"调用失败，已重试 {max_retries} 次。\n"
            f"服务商：{provider}｜模型：{model}\n"
            f"最后错误：{last_error}"
        )

    # ------------------------------------------------------------------
    # 图片输入
    # ------------------------------------------------------------------

    @staticmethod
    def encode_image(image_path: str | Path) -> str:
        """将图片转为 base64 字符串。"""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片不存在：{path}")

        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    @staticmethod
    def guess_mime_type(image_path: str | Path) -> str:
        """根据扩展名推断 MIME 类型。"""
        suffix = Path(image_path).suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".gif": "image/gif",
        }
        return mime_map.get(suffix, "image/jpeg")

    def chat_with_image(
        self,
        image_path: str | Path,
        prompt: str,
        model: str | None = None,
        temperature: float = 0.1,
    ) -> str:
        """带图片的多模态调用。

        Args:
            image_path: 本地图片路径
            prompt: 文字指令
            model: 视觉模型名称

        Returns:
            模型返回的文本

        Raises:
            LLMError: 配置的视觉服务商不支持图片输入（如 DeepSeek）
        """
        provider, _, model = self._resolve("vision", model)

        # 提前拦截：DeepSeek 没有视觉模型，配错了要给出明确提示，
        # 而不是等 API 返回一个看不懂的错误
        if provider == "deepseek":
            raise LLMError(
                "DeepSeek 目前没有视觉模型，无法识别图片。\n"
                "请在 .env 中把 VISION_PROVIDER 设为 dashscope 或 zhipu，\n"
                "并配置对应的 API Key（这两家都有免费额度）。\n"
                "文本分析仍然可以用 DeepSeek，两者互不影响。"
            )

        if not any(model.startswith(p) for p in VISION_MODEL_PREFIXES):
            if self.config.debug:
                print(
                    f"  [警告] 模型 {model} 看起来不是视觉模型，"
                    f"调用可能失败。请确认 VISION_MODEL 配置正确。"
                )

        b64 = self.encode_image(image_path)
        mime = self.guess_mime_type(image_path)

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        return self.chat(messages, model=model, temperature=temperature, role="vision")

    # ------------------------------------------------------------------
    # JSON 输出解析
    # ------------------------------------------------------------------

    @staticmethod
    def extract_json(text: str) -> dict[str, Any]:
        """从模型返回的文本中提取 JSON 对象。

        大模型的输出经常不干净，常见情况：
        1. 裹在 ```json ... ``` 代码块里
        2. 前后有多余的说明文字
        3. 直接是裸 JSON

        本方法依次尝试这三种情况。

        Args:
            text: 模型原始输出

        Returns:
            解析后的字典

        Raises:
            LLMError: 无法解析出合法 JSON
        """
        if not text or not text.strip():
            raise LLMError("模型返回内容为空")

        raw = text.strip()

        # 情况 1：提取 ```json ... ``` 代码块
        code_block = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
        if code_block:
            raw = code_block.group(1).strip()

        # 情况 2：直接尝试解析
        try:
            result = json.loads(raw)
            if isinstance(result, dict):
                return result
            # 如果模型返回了数组，包一层
            if isinstance(result, list):
                return {"items": result}
        except json.JSONDecodeError:
            pass

        # 情况 3：用括号配对法提取第一个完整的 JSON 对象
        # 比正则更可靠，因为能正确处理嵌套和字符串内的括号
        start = raw.find("{")
        if start != -1:
            depth = 0
            in_string = False
            escape = False

            for i in range(start, len(raw)):
                ch = raw[i]

                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue

                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = raw[start : i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError as exc:
                            raise LLMError(
                                f"提取到 JSON 结构但解析失败：{exc}\n原文片段：{candidate[:300]}"
                            ) from exc

        raise LLMError(f"无法从模型输出中提取 JSON。原文前 500 字：\n{raw[:500]}")

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.1,
        auto_repair: bool = True,
    ) -> dict[str, Any]:
        """调用模型并直接返回解析后的 JSON。

        Args:
            messages: 消息列表
            model: 模型名称
            temperature: 温度
            auto_repair: 解析失败时是否让模型自我修复一次

        Returns:
            解析后的字典
        """
        text = self.chat(messages, model=model, temperature=temperature, json_mode=True)

        try:
            return self.extract_json(text)
        except LLMError:
            if not auto_repair:
                raise

            if self.config.debug:
                print("  [JSON 解析失败，尝试让模型自我修复...]")

            repair_messages = [
                *messages,
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": (
                        "你刚才的输出不是合法 JSON。请只输出一个合法的 JSON 对象，"
                        "不要包含任何解释文字，不要用 markdown 代码块包裹。"
                    ),
                },
            ]

            repaired = self.chat(repair_messages, model=model, temperature=0)
            return self.extract_json(repaired)
