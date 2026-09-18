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


class LLMError(Exception):
    """大模型调用异常。"""


class LLMClient:
    """大模型客户端。"""

    def __init__(self, config: Config | None = None, timeout: float = 120.0):
        self.config = config or get_config()
        self.timeout = timeout

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
    ) -> str:
        """调用大模型，返回文本回复。

        Args:
            messages: OpenAI 格式的消息列表
            model: 模型名称，默认使用配置中的 text_model
            temperature: 温度。分析类任务建议低温度（0.1-0.3）以保证稳定性
            max_retries: 最大重试次数
            json_mode: 是否要求模型输出 JSON（部分服务商支持 response_format）

        Returns:
            模型返回的文本

        Raises:
            LLMError: 所有重试均失败
        """
        provider = self.config.llm_provider
        api_key = self.config.get_api_key()
        model = model or self.config.text_model
        url = ENDPOINTS.get(provider)

        if not url:
            raise LLMError(f"未知的服务商：{provider}")

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        # DeepSeek 不支持 response_format 的 json_object，其他两家支持
        if json_mode and provider in ("dashscope", "zhipu"):
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

        raise LLMError(f"调用失败，已重试 {max_retries} 次。最后错误：{last_error}")

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
        """
        b64 = self.encode_image(image_path)
        mime = self.guess_mime_type(image_path)
        model = model or self.config.vision_model

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

        return self.chat(messages, model=model, temperature=temperature)

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
