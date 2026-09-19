"""
命令行入口。

用法：
    python -m src.main <图片路径> [选项]

示例：
    python -m src.main data/samples/test1.jpg
    python -m src.main data/samples/test1.jpg --words 10 --json
    python -m src.main data/samples/test1.jpg --text-only
    python -m src.main --text "The book which I bought is interesting."

阶段 1 的产物。阶段 2 的 Web 界面见 src/web/app.py。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config import get_config
from src.llm_client import LLMError
from src.pipeline import STAGES, analyze_image, analyze_text, save_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="English-Windish：拍一张英语文本照片，得到语法解析与单词精讲",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python -m src.main data/samples/test1.jpg
  python -m src.main data/samples/test1.jpg --words 10
  python -m src.main data/samples/test1.jpg --json --output result.json
  python -m src.main --text "The book which I bought yesterday is interesting."
        """,
    )

    parser.add_argument("image", nargs="?", help="图片路径（与 --text 二选一）")
    parser.add_argument("--text", help="直接分析一段文本，跳过图片识别（用于调试）")
    parser.add_argument("--words", type=int, default=8, help="生成词卡的数量（默认 8）")
    parser.add_argument("--output", help="输出文件路径。不指定则自动生成到 data/reports/")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式而非 Markdown")
    parser.add_argument(
        "--text-only", action="store_true", help="只识别文字，不做语法分析（省额度）"
    )
    parser.add_argument("--no-words", action="store_true", help="跳过单词精讲")
    parser.add_argument("--quiet", action="store_true", help="不打印进度")

    return parser.parse_args()


def _make_printer(total: int):
    """生成一个把进度打印到终端的回调。"""

    def printer(step: int, _total: int, message: str) -> None:
        print(f"[{step}/{total}] {message}...")

    return printer


def main() -> int:
    args = parse_args()

    if not args.image and not args.text:
        print("错误：必须提供图片路径或 --text 文本。", file=sys.stderr)
        print("运行 `python -m src.main --help` 查看用法。", file=sys.stderr)
        return 1

    config = get_config()

    # 提前校验配置，避免跑到一半才报错
    problems = config.validate()
    if problems:
        print("\n配置有问题，请先修复：\n", file=sys.stderr)
        for i, problem in enumerate(problems, 1):
            print(f"{i}. {problem}\n", file=sys.stderr)
        print("提示：把 .env.example 复制为 .env，填入你的 API Key。\n", file=sys.stderr)
        return 1

    print("=" * 60)
    print("  English-Windish · 英语文本分析")
    print("=" * 60)
    print(f"  视觉：{config.vision_provider} / {config.vision_model}")
    print(f"  文本：{config.text_provider} / {config.text_model}")
    print()

    on_progress = None if args.quiet else _make_printer(len(STAGES))

    try:
        if args.text:
            result = analyze_text(
                args.text,
                word_count=args.words,
                no_words=args.no_words,
                on_progress=on_progress,
            )
        else:
            result = analyze_image(
                args.image,
                word_count=args.words,
                text_only=args.text_only,
                no_words=args.no_words,
                on_progress=on_progress,
            )
    except (LLMError, FileNotFoundError, ValueError) as exc:
        print(f"\n执行失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n\n已中断。", file=sys.stderr)
        return 130

    output_path = save_report(result, args.output, as_json=args.json)

    print()
    print("=" * 60)
    print(f"  完成！{result.summary()}")
    print(f"  报告已保存：{output_path}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
