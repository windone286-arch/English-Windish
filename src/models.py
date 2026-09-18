"""
数据模型定义。

本模块定义了整个应用的核心数据结构。所有模块（OCR、语法解析、单词精讲、报告生成）
都通过这些数据结构交换数据，而不是传递裸字典。

为什么用 dataclass 而不是 dict？
1. 字段有名字，写代码时能自动补全，不会拼错 key
2. 类型明确，IDE 能检查出错误
3. 可以挂方法（比如 to_dict / from_dict），转换逻辑集中在一处
4. 面试时能讲清楚"为什么这么设计"——这是工程素养的体现
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class GrammarType(str, Enum):
    """语法现象分类。

    继承 str 是为了让 JSON 序列化时直接输出字符串，不需要额外转换。
    """

    COLLOCATION = "固定搭配"
    COMPLEX_SENTENCE = "复杂句型"
    SPECIAL_PATTERN = "特殊句式"
    PHRASAL_VERB = "短语动词"
    IDIOM = "习语"


@dataclass
class GrammarPoint:
    """一处语法点。

    对应界面上高亮标注的一个片段。
    """

    text: str
    """原文中被标注的片段，如 "be accustomed to doing" """

    grammar_type: GrammarType
    """语法现象类型"""

    subtype: str = ""
    """更细的分类，如 "定语从句"、"倒装句"、"虚拟语气" """

    explanation: str = ""
    """中文解释：这里为什么这么用，是什么结构"""

    signal_words: list[str] = field(default_factory=list)
    """触发该语法现象的标志词，如 ["as", "though"] 之于倒装"""


@dataclass
class Sentence:
    """一个句子及其配套解析。

    这是界面上"逐句对照"的最小单元。
    """

    index: int
    """句子序号，从 1 开始"""

    original: str
    """英文原文"""

    translation: str = ""
    """中文翻译"""

    grammar_points: list[GrammarPoint] = field(default_factory=list)
    """本句中识别出的语法点"""

    structure: str = ""
    """句子主干结构，如 "主 + 谓 + 宾 + 定语从句" """


@dataclass
class WordForm:
    """一个词性下的释义集合。"""

    part_of_speech: str
    """词性，如 "n." / "v." / "adj." """

    definitions: list[str] = field(default_factory=list)
    """该词性下的中文释义列表"""


@dataclass
class Collocation:
    """一个固定搭配及其例句。"""

    phrase: str
    """搭配本身，如 "be accustomed to" """

    meaning: str
    """中文含义"""

    example: str = ""
    """英文例句"""

    example_translation: str = ""
    """例句的中文翻译"""


@dataclass
class WordEntry:
    """一个单词的完整词卡。

    这是"单词精讲"功能的核心数据结构，也是本项目区别于普通词典 App 的地方。
    """

    word: str
    """单词本身"""

    phonetic_uk: str = ""
    """英式音标"""

    phonetic_us: str = ""
    """美式音标"""

    # ---- 构词分析 ----
    morphology: str = ""
    """词根词缀拆解说明，如 "ac-（加强）+ custom（习惯）+ -ed → 习惯了的" """

    etymon: str = ""
    """词源 / 核心原意，帮助理解引申义的由来"""

    # ---- 释义 ----
    all_definitions: list[WordForm] = field(default_factory=list)
    """按词性分组的完整释义"""

    high_freq_definitions: list[str] = field(default_factory=list)
    """高频义项（默认展示，最多 3 条）"""

    # ---- 搭配 ----
    collocations: list[Collocation] = field(default_factory=list)
    """常见固定搭配，每条含例句与翻译"""

    # ---- 上下文 ----
    context_sentence: str = ""
    """该词在原文中出现的句子（帮助理解在本文中的具体含义）"""

    notes: str = ""
    """补充说明，如易混淆词辨析"""


@dataclass
class AnalysisResult:
    """一次完整分析的结果。

    这是整个应用的顶层数据结构，代表"用户拍一张照片后得到的全部内容"。
    """

    # ---- 元信息 ----
    source_image: str = ""
    """来源图片路径"""

    created_at: str = ""
    """分析时间，ISO 格式"""

    # ---- 识别结果 ----
    raw_text: str = ""
    """视觉模型识别出的英文原文"""

    # ---- 语法解析 ----
    sentences: list[Sentence] = field(default_factory=list)
    """逐句解析结果"""

    full_translation: str = ""
    """全文中文翻译"""

    # ---- 单词精讲 ----
    words: list[WordEntry] = field(default_factory=list)
    """重点词词卡列表"""

    # ---- 统计 ----
    stats: dict[str, Any] = field(default_factory=dict)
    """统计信息，如句子数、语法点数量、生词数"""

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的字典。"""
        return asdict(self)

    def summary(self) -> str:
        """生成一行摘要，便于命令行输出。"""
        grammar_count = sum(len(s.grammar_points) for s in self.sentences)
        return (
            f"识别 {len(self.sentences)} 句 | "
            f"语法点 {grammar_count} 处 | "
            f"生词 {len(self.words)} 个"
        )
