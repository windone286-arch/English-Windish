"""
单元测试。

这些测试**不调用真实 API**，只验证纯逻辑部分：
- 数据模型
- 文本切分
- JSON 提取
- 报告渲染

为什么这样设计？因为调用 API 的测试又慢又要花钱，而且在 CI 里跑不了。
把可测的纯逻辑抽出来单独测，是工程上的标准做法。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.grammar.analyzer import GrammarAnalyzer  # noqa: E402
from src.llm_client import LLMClient, LLMError  # noqa: E402
from src.models import (  # noqa: E402
    AnalysisResult,
    Collocation,
    GrammarPoint,
    GrammarType,
    Sentence,
    WordEntry,
    WordForm,
)
from src.pipeline import IMAGE_STAGES, STAGES, TEXT_STAGES  # noqa: E402
from src.report.generator import ReportGenerator  # noqa: E402


# ======================================================================
# Prompt 模板
# ======================================================================


class TestPrompts:
    """Prompt 模板测试。

    这里有一个真实的踩坑：Prompt 里含 JSON 示例的花括号，
    而 str.format() 会把 { } 当成占位符，导致 KeyError。
    修法是转义成 {{ }}。这组测试就是防止以后加新 Prompt 时重犯。
    """

    def test_all_templates_format_without_error(self):
        """所有 Prompt 模板都应该能用占位符正常格式化。

        如果某个模板里有未转义的花括号（比如 JSON 示例），
        这里会抛 KeyError，测试立刻失败。
        """
        from src import prompts as P

        cases = [
            (P.IMAGE_OCR, {}),
            (P.GRAMMAR_ANALYSIS, {"text": "sample"}),
            (P.KEYWORD_EXTRACTION, {"count": 5, "text": "sample"}),
            (P.WORD_DETAIL, {"word": "test", "context_hint": ""}),
            (
                P.STUDY_SUGGESTION,
                {
                    "sentence_count": 1,
                    "grammar_count": 1,
                    "grammar_types": "固定搭配",
                    "word_count": 1,
                },
            ),
        ]

        for template, kwargs in cases:
            # 不抛异常即通过
            template.format(**kwargs)

    def test_grammar_prompt_json_example_preserved(self):
        """转义后的 JSON 示例应该还原成正常的单个花括号。"""
        from src.prompts import GRAMMAR_ANALYSIS

        result = GRAMMAR_ANALYSIS.format(text="sample")
        # 格式化后应该是正常的 JSON 结构，而不是 {{ }}
        assert '"sentences": [' in result
        assert "{{" not in result

    def test_prompts_contain_required_placeholders(self):
        from src import prompts as P

        assert "{text}" in P.GRAMMAR_ANALYSIS
        assert "{count}" in P.KEYWORD_EXTRACTION
        assert "{word}" in P.WORD_DETAIL


# ======================================================================
# 配置
# ======================================================================


class TestConfig:
    """配置模块测试。

    重点验证「视觉与文本分离」这一设计——这是本项目的关键决策，
    因为 DeepSeek 有很强的文本能力但没有视觉模型。
    """

    def test_get_api_key_unknown_provider(self):
        from src.config import Config

        config = Config()
        with pytest.raises(ValueError, match="不支持的服务商"):
            config.get_api_key("不存在的服务商")

    def test_get_api_key_missing_gives_chinese_hint(self):
        from src.config import Config

        config = Config()
        with pytest.raises(ValueError) as exc_info:
            config.get_api_key("deepseek")

        message = str(exc_info.value)
        assert "未配置 DeepSeek" in message
        assert ".env" in message
        assert "DEEPSEEK_API_KEY" in message

    def test_get_api_key_returns_configured_key(self):
        from src.config import Config

        config = Config(deepseek_api_key="sk-test-123")
        assert config.get_api_key("deepseek") == "sk-test-123"

    def test_default_providers_split(self):
        """默认配置应该是视觉 dashscope + 文本 deepseek。"""
        from src.config import Config

        config = Config()
        assert config.vision_provider == "dashscope"
        assert config.text_provider == "deepseek"

    def test_validate_reports_both_missing_keys(self):
        from src.config import Config

        config = Config()
        problems = config.validate()
        # 两个 Key 都没配，应该报两条
        assert len(problems) == 2
        assert any("视觉" in p for p in problems)
        assert any("文本" in p for p in problems)

    def test_validate_rejects_deepseek_as_vision(self):
        """把 DeepSeek 配成视觉服务商时，必须给出明确错误。"""
        from src.config import Config

        config = Config(
            vision_provider="deepseek",
            deepseek_api_key="sk-test",
            text_provider="deepseek",
        )
        problems = config.validate()
        assert any("没有视觉模型" in p for p in problems)

    def test_validate_passes_when_configured(self):
        from src.config import Config

        config = Config(
            vision_provider="dashscope",
            text_provider="deepseek",
            dashscope_api_key="sk-vision",
            deepseek_api_key="sk-text",
        )
        assert config.validate() == []


# ======================================================================
# 数据模型
# ======================================================================


class TestModels:
    def test_sentence_summary_counts_grammar_points(self):
        """summary 应该正确统计语法点数量。"""
        s1 = Sentence(index=1, original="Hello world.")
        s1.grammar_points.append(
            GrammarPoint(text="Hello", grammar_type=GrammarType.COLLOCATION)
        )

        s2 = Sentence(index=2, original="Goodbye world.")

        result = AnalysisResult(sentences=[s1, s2])
        assert result.summary() == "识别 2 句 | 语法点 1 处 | 生词 0 个"

    def test_to_dict_serializable(self):
        """to_dict 的结果必须能被 JSON 序列化（枚举要转成字符串）。"""
        import json

        s = Sentence(index=1, original="Test.")
        s.grammar_points.append(
            GrammarPoint(text="Test", grammar_type=GrammarType.SPECIAL_PATTERN)
        )
        result = AnalysisResult(sentences=[s])

        payload = result.to_dict()
        text = json.dumps(payload, ensure_ascii=False)
        assert "特殊句式" in text

    def test_grammar_type_is_str_enum(self):
        """GrammarType 继承 str，可以直接和字符串比较。"""
        assert GrammarType.COLLOCATION == "固定搭配"
        assert GrammarType.COMPLEX_SENTENCE.value == "复杂句型"


# ======================================================================
# 文本切分
# ======================================================================


class TestTextSplitting:
    def test_short_text_single_chunk(self):
        text = "This is short. Very short."
        assert len(GrammarAnalyzer._split_text(text)) == 1

    def test_long_text_split(self):
        text = "This is a sentence that repeats. " * 200
        chunks = GrammarAnalyzer._split_text(text)
        assert len(chunks) > 1
        # 每块都不应超过上限太多（允许超出一个句子的长度）
        for chunk in chunks:
            assert len(chunk) < 3500

    def test_paragraph_split_preserves_content(self):
        """切分不应该丢失任何内容。"""
        text = "First paragraph here.\n\nSecond paragraph here.\n\nThird one."
        chunks = GrammarAnalyzer._split_text(text)
        joined = " ".join(chunks)
        assert "First paragraph" in joined
        assert "Second paragraph" in joined
        assert "Third one" in joined

    def test_abbreviation_not_split(self):
        """缩写中的句点不应该被当作句子边界。"""
        text = "Mr. Smith went to the U.S. last year."
        parts = GrammarAnalyzer._split_by_sentence(text, max_len=1000)
        assert len(parts) == 1
        assert "Mr. Smith" in parts[0]
        assert "U.S." in parts[0]

    def test_sentence_split_by_punctuation(self):
        """按句末标点切分。

        注意：_split_by_sentence 的语义是"把句子按 max_len 累加成块"，
        不是"一个句子一块"。所以只有 max_len 小到装不下两句时才会分开。
        """
        text = "First sentence. Second sentence! Third sentence?"
        parts = GrammarAnalyzer._split_by_sentence(text, max_len=20)
        assert len(parts) == 3
        assert parts[0] == "First sentence."
        assert parts[1] == "Second sentence!"
        assert parts[2] == "Third sentence?"

    def test_sentence_split_respects_max_len(self):
        """max_len 足够大时，多句应合并成一块。"""
        text = "First sentence. Second sentence! Third sentence?"
        parts = GrammarAnalyzer._split_by_sentence(text, max_len=1000)
        assert len(parts) == 1


# ======================================================================
# JSON 提取（抗模型输出污染）
# ======================================================================


class TestJsonExtraction:
    def test_plain_json(self):
        text = '{"a": 1, "b": "hello"}'
        assert LLMClient.extract_json(text) == {"a": 1, "b": "hello"}

    def test_json_in_code_block(self):
        text = '```json\n{"a": 1}\n```'
        assert LLMClient.extract_json(text) == {"a": 1}

    def test_json_with_preamble(self):
        """模型经常在前面加废话。"""
        text = '好的，以下是分析结果：\n{"a": 1}\n希望有帮助。'
        assert LLMClient.extract_json(text) == {"a": 1}

    def test_nested_json(self):
        text = '{"outer": {"inner": {"deep": [1, 2, 3]}}}'
        result = LLMClient.extract_json(text)
        assert result["outer"]["inner"]["deep"] == [1, 2, 3]

    def test_braces_inside_string(self):
        """字符串里的花括号不应该干扰括号配对。"""
        text = '{"text": "use {braces} carefully", "n": 1}'
        result = LLMClient.extract_json(text)
        assert result["text"] == "use {braces} carefully"
        assert result["n"] == 1

    def test_escaped_quotes_inside_string(self):
        text = '{"text": "he said \\"hi\\""}'
        result = LLMClient.extract_json(text)
        assert result["text"] == 'he said "hi"'

    def test_empty_raises(self):
        with pytest.raises(LLMError):
            LLMClient.extract_json("")

    def test_no_json_raises(self):
        with pytest.raises(LLMError):
            LLMClient.extract_json("这里完全没有 JSON。")


# ======================================================================
# 语法点校验（幻觉过滤）
# ======================================================================


class TestGrammarValidation:
    def setup_method(self):
        # 不传 client 会去读配置，这里造一个最小可用对象
        self.analyzer = GrammarAnalyzer.__new__(GrammarAnalyzer)

        class FakeConfig:
            debug = False

        class FakeClient:
            config = FakeConfig()

        self.analyzer.client = FakeClient()

    def test_hallucinated_span_dropped(self):
        """模型标注的片段不在原句中时，应该被丢弃。"""
        raw = [
            {
                "index": 1,
                "original": "The book is interesting.",
                "translation": "这本书很有趣。",
                "grammar_points": [
                    {
                        "text": "which I bought",  # 原句里根本没有
                        "grammar_type": "复杂句型",
                        "explanation": "编造的",
                    }
                ],
            }
        ]
        sentences = self.analyzer._parse_sentences(raw)
        assert len(sentences) == 1
        assert sentences[0].grammar_points == []

    def test_valid_span_kept(self):
        raw = [
            {
                "index": 1,
                "original": "The book which I bought is interesting.",
                "translation": "我买的那本书很有趣。",
                "grammar_points": [
                    {
                        "text": "which I bought",
                        "grammar_type": "复杂句型",
                        "subtype": "定语从句",
                        "explanation": "修饰 the book",
                        "signal_words": ["which"],
                    }
                ],
            }
        ]
        sentences = self.analyzer._parse_sentences(raw)
        assert len(sentences[0].grammar_points) == 1
        gp = sentences[0].grammar_points[0]
        assert gp.text == "which I bought"
        assert gp.grammar_type == GrammarType.COMPLEX_SENTENCE
        assert gp.signal_words == ["which"]

    def test_invalid_grammar_type_falls_back(self):
        """未知的语法类型应该回退到默认值，而不是崩溃。"""
        raw = [
            {
                "index": 1,
                "original": "Test sentence.",
                "grammar_points": [
                    {"text": "Test", "grammar_type": "根本不存在的类型"}
                ],
            }
        ]
        sentences = self.analyzer._parse_sentences(raw)
        assert sentences[0].grammar_points[0].grammar_type == GrammarType.COLLOCATION

    def test_grammar_points_sorted_by_position(self):
        """语法点应按在原句中的出现位置排序，方便界面按顺序高亮。"""
        raw = [
            {
                "index": 1,
                "original": "Not only did he pass, but he also won.",
                "grammar_points": [
                    {"text": "but he also won", "grammar_type": "固定搭配"},
                    {"text": "Not only did he", "grammar_type": "特殊句式"},
                ],
            }
        ]
        sentences = self.analyzer._parse_sentences(raw)
        points = sentences[0].grammar_points
        assert points[0].text == "Not only did he"
        assert points[1].text == "but he also won"

    def test_empty_original_skipped(self):
        raw = [{"index": 1, "original": "   ", "translation": "空"}]
        assert self.analyzer._parse_sentences(raw) == []


# ======================================================================
# 报告渲染
# ======================================================================


class TestPipelineStages:
    """流水线的阶段定义。

    图片链路 4 步、文本链路 3 步，两者不能混用——混用会让文本分析的
    进度条出现「识别图片文字」这个根本不会执行的步骤，用户会以为卡住了。
    """

    def test_image_pipeline_has_four_stages(self):
        assert len(IMAGE_STAGES) == 4
        assert IMAGE_STAGES[0] == "识别图片文字"

    def test_text_pipeline_skips_image_recognition(self):
        assert len(TEXT_STAGES) == 3
        assert "识别图片文字" not in TEXT_STAGES
        assert TEXT_STAGES[0] == "分析语法结构"

    def test_text_stages_are_tail_of_image_stages(self):
        """文本阶段应当是图片阶段去掉第一步后的余下部分。

        这条断言的作用是防止两套定义各自漂移：将来改了图片那侧的文案，
        文本这边忘了同步，测试就会失败。
        """
        assert TEXT_STAGES == IMAGE_STAGES[1:]

    def test_stages_alias_points_to_image(self):
        """STAGES 是历史别名，必须指向图片阶段。"""
        assert STAGES is IMAGE_STAGES


class TestReportGenerator:
    def _make_result(self) -> AnalysisResult:
        s = Sentence(
            index=1,
            original="The book which I bought is interesting.",
            translation="我买的那本书很有趣。",
            structure="主 + 定从 + 系表",
        )
        s.grammar_points.append(
            GrammarPoint(
                text="which I bought",
                grammar_type=GrammarType.COMPLEX_SENTENCE,
                subtype="定语从句",
                explanation="修饰 the book",
                signal_words=["which"],
            )
        )

        w = WordEntry(
            word="interesting",
            phonetic_uk="/ˈɪntrəstɪŋ/",
            morphology="interest + -ing",
            etymon="源自拉丁语 inter esse（在其中）",
            high_freq_definitions=["adj. 有趣的"],
            all_definitions=[
                WordForm(part_of_speech="adj.", definitions=["有趣的", "引人入胜的"])
            ],
            collocations=[
                Collocation(
                    phrase="be interested in",
                    meaning="对...感兴趣",
                    example="I am interested in music.",
                    example_translation="我对音乐感兴趣。",
                )
            ],
        )

        return AnalysisResult(
            source_image="demo.jpg",
            created_at="2026-09-19 03:00:00",
            raw_text="The book which I bought is interesting.",
            sentences=[s],
            full_translation="我买的那本书很有趣。",
            words=[w],
        )

    def test_markdown_contains_key_sections(self):
        md = ReportGenerator().to_markdown(self._make_result())
        assert "# 英语文本分析报告" in md
        assert "## 一、原文" in md
        assert "## 二、逐句语法解析" in md
        assert "## 三、全文翻译" in md
        assert "## 四、单词精讲" in md

    def test_markdown_contains_grammar_marker(self):
        md = ReportGenerator().to_markdown(self._make_result())
        assert "`which I bought`" in md
        assert "定语从句" in md

    def test_markdown_contains_collocation_with_example(self):
        md = ReportGenerator().to_markdown(self._make_result())
        assert "be interested in" in md
        assert "I am interested in music." in md
        assert "我对音乐感兴趣。" in md

    def test_markdown_foldable_definitions(self):
        """完整释义应该放在折叠块里。"""
        md = ReportGenerator().to_markdown(self._make_result())
        assert "<details>" in md
        assert "展开查看全部释义" in md

    def test_json_round_trip(self):
        import json

        result = self._make_result()
        payload = json.loads(ReportGenerator().to_json(result))
        assert payload["sentences"][0]["original"] == result.sentences[0].original
        assert payload["words"][0]["word"] == "interesting"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
