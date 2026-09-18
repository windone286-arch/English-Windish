"""
主程序入口（阶段 1：命令行版本）。

用法：
    python -m src.main <图片路径> [选项]

示例：
    python -m src.main data/samples/test.jpg
    python -m src.main data/samples/test.jpg --words 10 --json
    python -m src.main data/samples/test.jpg --text-only

这个阶段的目标是**把全链路跑通**，不做界面。
界面在阶段 2 用 Web 实现。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from src.config import get_config
from src.grammar.analyzer import GrammarAnalyzer
from src.llm_client import LLMError
from src.models import AnalysisResult
from src.ocr.recognizer import ImageRecognizer
from src.report.generator import ReportGenerator
from src.vocabulary.tutor import VocabularyTutor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="English-Windish：拍一张英语文本照片，得到语法解析与单词精讲",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python -m src.main data/samples/test.jpg
  python -m src.main data/samples/test.jpg --words 10
  python -m src.main data/samples/test.jpg --json --output result.json
  python -m src.main --text "The book which I bought yesterday is interesting."
        """,
    )

    parser.add_argument(
        "image",
        nargs="?",
        help="图片路径（与 --text 二选一）",
    )
    parser.add_argument(
        "--text",
        help="直接分析一段文本，跳过图片识别（用于调试）",
    )
    parser.add_argument(
        "--words",
        type=int,
        default=8,
        help="生成词卡的数量（默认 8）",
    )
    parser.add_argument(
        "--output",
        help="输出文件路径。不指定则自动生成到 data/reports/",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式而非 Markdown",
    )
    parser.add_argument(
        "--text-only",
        action="store_true",
        help="只识别文字，不做语法分析（省额度，用于快速验证图片质量）",
    )
    parser.add_argument(
        "--no-words",
        action="store_true",
        help="跳过单词精讲",
    )

    return parser.parse_args()


def analyze_image(
    image_path: str,
    word_count: int = 8,
    text_only: bool = False,
    no_words: bool = False,
    debug: bool = True,
) -> AnalysisResult:
    """完整的分析流程。

    Args:
        image_path: 图片路径
        word_count: 生成词卡数量
        text_only: 只做识别
        no_words: 跳过单词精讲
        debug: 是否打印进度

    Returns:
        AnalysisResult
    """
    result = AnalysisResult(
        source_image=str(image_path),
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # ---- 步骤 1：识别图片 ----
    if debug:
        print("\n[1/4] 识别图片文字...")

    recognizer = ImageRecognizer()
    result.raw_text = recognizer.recognize(image_path)

    if text_only:
        return result

    # ---- 步骤 2：语法分析 ----
    if debug:
        print("\n[2/4] 分析语法结构...")

    analyzer = GrammarAnalyzer()
    sentences, translation = analyzer.analyze(result.raw_text)
    result.sentences = sentences
    result.full_translation = translation

    # ---- 步骤 3：单词精讲 ----
    if not no_words:
        if debug:
            print(f"\n[3/4] 生成 {word_count} 个单词的词卡...")

        tutor = VocabularyTutor()
        result.words = tutor.process(result.raw_text, count=word_count)
    elif debug:
        print("\n[3/4] 已跳过单词精讲")

    # ---- 步骤 4：统计 ----
    if debug:
        print("\n[4/4] 汇总结果...")

    grammar_count = sum(len(s.grammar_points) for s in result.sentences)
    grammar_types: dict[str, int] = {}
    for sentence in result.sentences:
        for gp in sentence.grammar_points:
            key = gp.grammar_type.value
            grammar_types[key] = grammar_types.get(key, 0) + 1

    result.stats = {
        "sentence_count": len(result.sentences),
        "grammar_count": grammar_count,
        "grammar_types": grammar_types,
        "word_count": len(result.words),
        "text_length": len(result.raw_text),
    }

    return result


def analyze_text(
    text: str,
    word_count: int = 8,
    no_words: bool = False,
    debug: bool = True,
) -> AnalysisResult:
    """直接分析文本（跳过图片识别）。"""
    result = AnalysisResult(
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        raw_text=text,
    )

    if debug:
        print("\n[1/3] 分析语法结构...")

    analyzer = GrammarAnalyzer()
    sentences, translation = analyzer.analyze(text)
    result.sentences = sentences
    result.full_translation = translation

    if not no_words:
        if debug:
            print(f"\n[2/3] 生成 {word_count} 个单词的词卡...")
        tutor = VocabularyTutor()
        result.words = tutor.process(text, count=word_count)

    if debug:
        print("\n[3/3] 汇总结果...")

    result.stats = {
        "sentence_count": len(result.sentences),
        "grammar_count": sum(len(s.grammar_points) for s in result.sentences),
        "word_count": len(result.words),
        "text_length": len(text),
    }

    return result


def main() -> int:
    args = parse_args()

    if not args.image and not args.text:
        print("错误：必须提供图片路径或 --text 文本。", file=sys.stderr)
        print("运行 `python -m src.main --help` 查看用法。", file=sys.stderr)
        return 1

    config = get_config()

    # 提前校验 API Key，避免跑到一半才报错
    try:
        config.get_api_key()
    except ValueError as exc:
        print(f"\n配置错误：\n{exc}\n", file=sys.stderr)
        return 1

    print("=" * 60)
    print("  English-Windish · 英语文本分析")
    print("=" * 60)

    try:
        if args.text:
            result = analyze_text(
                args.text,
                word_count=args.words,
                no_words=args.no_words,
                debug=config.debug,
            )
        else:
            result = analyze_image(
                args.image,
                word_count=args.words,
                text_only=args.text_only,
                no_words=args.no_words,
                debug=config.debug,
            )
    except (LLMError, FileNotFoundError, ValueError) as exc:
        print(f"\n执行失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n\n已中断。", file=sys.stderr)
        return 130

    # ---- 输出 ----
    generator = ReportGenerator()

    if args.output:
        output_path = Path(args.output)
    else:
        suffix = ".json" if args.json else ".md"
        output_path = generator.default_output_path(suffix=suffix)

    if args.json:
        generator.save_json(result, output_path)
    else:
        generator.save_markdown(result, output_path)

    print()
    print("=" * 60)
    print(f"  完成！{result.summary()}")
    print(f"  报告已保存：{output_path}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
