"""
报告生成模块。

职责：把 AnalysisResult 渲染成人类可读的输出。

支持两种格式：
1. **Markdown**：便于阅读、分享、存档，也是命令行版的主要输出
2. **JSON**：便于程序消费、后续接 Web 前端

为什么不直接在模型里生成报告？
因为模型生成的排版不稳定，而我们的数据已经是结构化的了。
**结构化数据 → 本地模板渲染**，比让模型再写一遍更可靠、更省钱。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.models import AnalysisResult, GrammarType, Sentence, WordEntry

# 语法类型的显示颜色（终端 ANSI，Markdown 里不用）
TYPE_MARKERS = {
    GrammarType.COLLOCATION: "🔗",
    GrammarType.COMPLEX_SENTENCE: "🧩",
    GrammarType.SPECIAL_PATTERN: "⭐",
    GrammarType.PHRASAL_VERB: "➡️",
    GrammarType.IDIOM: "💬",
}


class ReportGenerator:
    """报告生成器。"""

    def to_markdown(self, result: AnalysisResult) -> str:
        """生成 Markdown 格式报告。"""
        lines: list[str] = []

        # ---- 标题区 ----
        lines.append("# 英语文本分析报告")
        lines.append("")
        if result.source_image:
            lines.append(f"> 来源图片：`{Path(result.source_image).name}`")
        lines.append(f"> 分析时间：{result.created_at}")
        lines.append(f"> {result.summary()}")
        lines.append("")

        # ---- 原文区 ----
        lines.append("## 一、原文")
        lines.append("")
        lines.append("```text")
        lines.append(result.raw_text.strip())
        lines.append("```")
        lines.append("")

        # ---- 语法解析区 ----
        lines.append("## 二、逐句语法解析")
        lines.append("")

        if not result.sentences:
            lines.append("*未解析出句子。*")
            lines.append("")
        else:
            for sentence in result.sentences:
                lines.extend(self._render_sentence(sentence))

        # ---- 全文翻译 ----
        if result.full_translation.strip():
            lines.append("## 三、全文翻译")
            lines.append("")
            lines.append(result.full_translation.strip())
            lines.append("")

        # ---- 单词精讲区 ----
        lines.append("## 四、单词精讲")
        lines.append("")

        if not result.words:
            lines.append("*本次未生成单词卡片。*")
            lines.append("")
        else:
            for word in result.words:
                lines.extend(self._render_word(word))

        # ---- 页脚 ----
        lines.append("---")
        lines.append("")
        lines.append("*本报告由 English-Windish 自动生成。*")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 句子渲染
    # ------------------------------------------------------------------

    def _render_sentence(self, sentence: Sentence) -> list[str]:
        lines: list[str] = []

        lines.append(f"### 第 {sentence.index} 句")
        lines.append("")
        lines.append(f"**原文**：{sentence.original}")
        lines.append("")
        lines.append(f"**翻译**：{sentence.translation}")

        if sentence.structure:
            lines.append("")
            lines.append(f"**主干结构**：{sentence.structure}")

        if sentence.grammar_points:
            lines.append("")
            lines.append("**语法点**：")
            lines.append("")
            for gp in sentence.grammar_points:
                marker = TYPE_MARKERS.get(gp.grammar_type, "•")
                type_label = gp.grammar_type.value
                if gp.subtype:
                    type_label += f"（{gp.subtype}）"

                lines.append(f"- {marker} **`{gp.text}`** ｜ {type_label}")
                if gp.explanation:
                    lines.append(f"  - {gp.explanation}")
                if gp.signal_words:
                    words = "、".join(f"`{w}`" for w in gp.signal_words)
                    lines.append(f"  - 标志词：{words}")

        lines.append("")
        return lines

    # ------------------------------------------------------------------
    # 单词渲染
    # ------------------------------------------------------------------

    def _render_word(self, word: WordEntry) -> list[str]:
        lines: list[str] = []

        # 标题 + 音标
        title = f"### {word.word}"
        if word.phonetic_uk or word.phonetic_us:
            phonetics = []
            if word.phonetic_uk:
                phonetics.append(f"英 {word.phonetic_uk}")
            if word.phonetic_us:
                phonetics.append(f"美 {word.phonetic_us}")
            title += f" ｜ {' / '.join(phonetics)}"
        lines.append(title)
        lines.append("")

        # 语境
        if word.context_sentence:
            lines.append(f"> **本文语境**：{word.context_sentence}")
            lines.append("")

        # 构词
        if word.morphology:
            lines.append(f"**构词分析**：{word.morphology}")
            lines.append("")

        # 词源
        if word.etymon:
            lines.append(f"**词源与核心原意**：{word.etymon}")
            lines.append("")

        # 释义
        if word.high_freq_definitions:
            lines.append("**高频释义**：")
            lines.append("")
            for d in word.high_freq_definitions:
                lines.append(f"- {d}")
            lines.append("")

        # 完整释义（折叠区）
        all_flat = self._flatten_definitions(word)
        if all_flat and len(all_flat) > len(word.high_freq_definitions):
            lines.append("<details>")
            lines.append("<summary>展开查看全部释义</summary>")
            lines.append("")
            for form in word.all_definitions:
                if not form.definitions:
                    continue
                lines.append(f"- **{form.part_of_speech}**")
                for d in form.definitions:
                    lines.append(f"  - {d}")
            lines.append("")
            lines.append("</details>")
            lines.append("")

        # 固定搭配
        if word.collocations:
            lines.append("**固定搭配**：")
            lines.append("")
            for col in word.collocations:
                lines.append(f"- **`{col.phrase}`** —— {col.meaning}")
                if col.example:
                    lines.append(f"  - {col.example}")
                if col.example_translation:
                    lines.append(f"  - {col.example_translation}")
            lines.append("")

        # 补充说明
        if word.notes:
            lines.append(f"**补充说明**：{word.notes}")
            lines.append("")

        lines.append("---")
        lines.append("")

        return lines

    @staticmethod
    def _flatten_definitions(word: WordEntry) -> list[str]:
        """把所有词性的释义拍平成一个列表，用于计数。"""
        result: list[str] = []
        for form in word.all_definitions:
            result.extend(form.definitions)
        return result

    # ------------------------------------------------------------------
    # JSON 输出
    # ------------------------------------------------------------------

    def to_json(self, result: AnalysisResult, indent: int = 2) -> str:
        """生成 JSON 格式报告。"""
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=indent)

    # ------------------------------------------------------------------
    # 文件保存
    # ------------------------------------------------------------------

    def save_markdown(self, result: AnalysisResult, output_path: str | Path) -> Path:
        """保存 Markdown 报告到文件。"""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_markdown(result), encoding="utf-8")
        return path

    def save_json(self, result: AnalysisResult, output_path: str | Path) -> Path:
        """保存 JSON 报告到文件。"""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(result), encoding="utf-8")
        return path

    @staticmethod
    def default_output_path(prefix: str = "report", suffix: str = ".md") -> Path:
        """生成带时间戳的默认输出路径。"""
        from src.config import get_config

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = get_config().data_dir / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"{prefix}_{timestamp}{suffix}"
