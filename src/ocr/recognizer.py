"""
图像识别模块。

职责：把一张图片变成干净的英文文本。

技术选择说明：
本项目**不使用传统 OCR**（如 Tesseract、PaddleOCR），而是直接把图片喂给
多模态大模型。原因：

1. **手写体识别**：学生试卷上常常是手写答案或手写笔记，传统 OCR 对手写体
   识别率极低（经常低于 50%），而多模态大模型能结合上下文推断
2. **版面理解**：试卷有分栏、题号、选项、印刷体与手写体混排，传统 OCR 会
   把版面结构搞乱，大模型能理解"这是一道选择题"
3. **抗噪能力**：试卷照片常有阴影、倾斜、折痕，传统 OCR 容易产生大量乱码

代价是需要联网并消耗 API 额度。但识别质量是这条流水线的起点——
起点错了，后面全错。
"""

from __future__ import annotations

from pathlib import Path

from src.llm_client import LLMClient, LLMError
from src.prompts import IMAGE_OCR


class ImageRecognizer:
    """图片文字识别器。"""

    # 支持的图片格式
    SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

    # 单张图片大小上限（字节）。超限的图片先压缩再上传，避免请求过大
    MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

    def __init__(self, client: LLMClient | None = None):
        self.client = client or LLMClient()

    def recognize(self, image_path: str | Path) -> str:
        """识别图片中的英文文本。

        Args:
            image_path: 图片路径

        Returns:
            识别出的文本

        Raises:
            FileNotFoundError: 图片不存在
            ValueError: 格式不支持或文件过大
            LLMError: 模型调用失败
        """
        path = Path(image_path)

        # ---- 前置校验 ----
        if not path.exists():
            raise FileNotFoundError(
                f"图片不存在：{path}\n"
                f"请确认路径是否正确。当前工作目录：{Path.cwd()}"
            )

        if path.suffix.lower() not in self.SUPPORTED_FORMATS:
            supported = "、".join(sorted(self.SUPPORTED_FORMATS))
            raise ValueError(
                f"不支持的图片格式：{path.suffix}\n支持格式：{supported}"
            )

        size_mb = path.stat().st_size / 1024 / 1024
        if path.stat().st_size > self.MAX_SIZE_BYTES:
            raise ValueError(
                f"图片过大：{size_mb:.1f} MB，上限 10 MB。\n"
                f"建议先用图片工具压缩，或裁剪掉不需要的部分。"
            )

        if self.client.config.debug:
            print(f"  [识别] {path.name}（{size_mb:.1f} MB）")

        # ---- 调用视觉模型 ----
        text = self.client.chat_with_image(path, IMAGE_OCR)
        text = self._clean(text)

        if not text.strip():
            raise LLMError(
                "识别结果为空。可能原因：图片中没有可识别的文字，"
                "或图片过于模糊。请换一张更清晰的图片重试。"
            )

        if self.client.config.debug:
            print(f"  [识别完成] {len(text)} 字符")

        return text

    @staticmethod
    def _clean(text: str) -> str:
        """清理模型输出中的杂质。

        模型有时会不听话地加上"以下是识别结果："这样的前缀，或者用代码块包裹。
        """
        cleaned = text.strip()

        # 剥离 ```...``` 包裹
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # 去掉第一行（``` 或 ```text）和最后一行（```）
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            cleaned = "\n".join(lines)

        # 剥离常见前缀
        prefixes = [
            "以下是识别结果：",
            "识别结果：",
            "以下是图片中的文本：",
            "图片中的文本：",
            "Here is the text:",
            "The text in the image is:",
        ]
        for prefix in prefixes:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :].strip()
                break

        return cleaned.strip()
