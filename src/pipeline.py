"""
分析流水线。

职责：把「图片 → 识别 → 语法解析 → 单词精讲 → 结果」这条链路封装成一个
可复用的函数，供命令行（src/main.py）和 Web 服务（src/web/app.py）共同调用。

为什么要把这段逻辑从 main.py 抽出来？
1. **避免重复**：Web 版和命令行版需要完全相同的分析逻辑，复制一遍必然出现
   「改了一处忘了另一处」的问题
2. **支持进度回调**：Web 需要实时反馈进度，命令行只需要打印。
   用回调函数把「进度如何呈现」和「分析如何进行」解耦
3. **便于测试**：流水线可以单独测试，不用启动 Web 服务器

这是典型的「关注点分离」——流水线只负责算，不负责怎么显示。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from src.grammar.analyzer import GrammarAnalyzer
from src.models import AnalysisResult
from src.ocr.recognizer import ImageRecognizer
from src.report.generator import ReportGenerator
from src.vocabulary.tutor import VocabularyTutor

# 进度回调的类型别名：(当前步骤, 总步骤数, 说明文字)
ProgressCallback = Callable[[int, int, str], None]

# 流水线的阶段定义，供前端展示进度条
STAGES = [
    "识别图片文字",
    "分析语法结构",
    "生成单词词卡",
    "汇总分析结果",
]


def _noop(step: int, total: int, message: str) -> None:
    """默认的进度回调：什么都不做。"""


def analyze_image(
    image_path: str | Path,
    word_count: int = 8,
    text_only: bool = False,
    no_words: bool = False,
    on_progress: ProgressCallback | None = None,
) -> AnalysisResult:
    """完整的图片分析流程。

    Args:
        image_path: 图片路径
        word_count: 生成词卡数量
        text_only: 只做识别，跳过后续分析（省 API 额度）
        no_words: 跳过单词精讲
        on_progress: 进度回调，签名 (step, total, message)

    Returns:
        AnalysisResult
    """
    report = on_progress or _noop
    total = len(STAGES)

    result = AnalysisResult(
        source_image=str(image_path),
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # ---- 步骤 1：识别图片 ----
    report(1, total, STAGES[0])
    recognizer = ImageRecognizer()
    result.raw_text = recognizer.recognize(image_path)

    if text_only:
        report(total, total, "完成")
        return result

    # ---- 步骤 2：语法分析 ----
    report(2, total, STAGES[1])
    analyzer = GrammarAnalyzer()
    sentences, translation = analyzer.analyze(result.raw_text)
    result.sentences = sentences
    result.full_translation = translation

    # ---- 步骤 3：单词精讲 ----
    if not no_words:
        report(3, total, STAGES[2])
        tutor = VocabularyTutor()
        result.words = tutor.process(result.raw_text, count=word_count)
    else:
        report(3, total, "已跳过单词精讲")

    # ---- 步骤 4：汇总 ----
    report(4, total, STAGES[3])
    result.stats = _build_stats(result)

    return result


def analyze_text(
    text: str,
    word_count: int = 8,
    no_words: bool = False,
    on_progress: ProgressCallback | None = None,
) -> AnalysisResult:
    """直接分析文本（跳过图片识别）。

    用途：调试 Prompt 时不用每次都拍照、走一遍识别，省时间也省额度。

    Args:
        text: 英文文本
        word_count: 生成词卡数量
        no_words: 跳过单词精讲
        on_progress: 进度回调

    Returns:
        AnalysisResult
    """
    report = on_progress or _noop

    result = AnalysisResult(
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        raw_text=text,
    )

    report(1, 3, "分析语法结构")
    analyzer = GrammarAnalyzer()
    sentences, translation = analyzer.analyze(text)
    result.sentences = sentences
    result.full_translation = translation

    if not no_words:
        report(2, 3, "生成单词词卡")
        tutor = VocabularyTutor()
        result.words = tutor.process(text, count=word_count)

    report(3, 3, "汇总分析结果")
    result.stats = _build_stats(result)

    return result


def _build_stats(result: AnalysisResult) -> dict[str, Any]:
    """统计各类数据，供报告头部和前端概览展示。"""
    grammar_types: dict[str, int] = {}
    for sentence in result.sentences:
        for point in sentence.grammar_points:
            key = point.grammar_type.value
            grammar_types[key] = grammar_types.get(key, 0) + 1

    return {
        "sentence_count": len(result.sentences),
        "grammar_count": sum(len(s.grammar_points) for s in result.sentences),
        "grammar_types": grammar_types,
        "word_count": len(result.words),
        "collocation_count": sum(len(w.collocations) for w in result.words),
        "text_length": len(result.raw_text),
    }


def save_report(
    result: AnalysisResult,
    output_path: str | Path | None = None,
    as_json: bool = False,
) -> Path:
    """保存报告到文件。

    Args:
        result: 分析结果
        output_path: 输出路径，不指定则自动生成到 data/reports/
        as_json: 是否保存为 JSON

    Returns:
        实际保存的路径
    """
    generator = ReportGenerator()

    if output_path is None:
        suffix = ".json" if as_json else ".md"
        output_path = generator.default_output_path(suffix=suffix)

    if as_json:
        return generator.save_json(result, output_path)
    return generator.save_markdown(result, output_path)
