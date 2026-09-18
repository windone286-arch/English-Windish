"""
语法解析模块。

职责：
1. 把整段文本拆成句子，逐句翻译
2. 标注每句中的固定搭配 / 复杂句型 / 特殊句式
3. 描述句子主干结构

关键设计：
- **分块处理**：长文本一次性丢给模型会超出上下文或导致质量下降，
  所以先按段落切分，逐块分析，再合并结果。
- **片段校验**：模型标注的 `text` 字段必须能在原句中找到。找不到的标注
  视为幻觉，直接丢弃。这是保证结果可信的最后一道防线。
"""

from __future__ import annotations

from src.llm_client import LLMClient, LLMError
from src.models import GrammarPoint, GrammarType, Sentence
from src.prompts import GRAMMAR_ANALYSIS

# 单次发送给模型的文本长度上限（字符）。超过则分块。
MAX_CHUNK_CHARS = 2500


class GrammarAnalyzer:
    """语法解析器。"""

    # 合法的语法类型集合，用于校验模型输出
    VALID_TYPES = {t.value for t in GrammarType}

    def __init__(self, client: LLMClient | None = None):
        self.client = client or LLMClient()

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def analyze(self, text: str) -> tuple[list[Sentence], str]:
        """分析文本的语法结构。

        Args:
            text: 英文文本

        Returns:
            (句子列表, 全文翻译)
        """
        if not text.strip():
            return [], ""

        chunks = self._split_text(text)

        if self.client.config.debug:
            print(f"  [语法分析] 文本 {len(text)} 字符，切分为 {len(chunks)} 块")

        all_sentences: list[Sentence] = []
        translations: list[str] = []
        index_offset = 0

        for i, chunk in enumerate(chunks, 1):
            if self.client.config.debug:
                print(f"  [语法分析] 处理第 {i}/{len(chunks)} 块...")

            sentences, translation = self._analyze_chunk(chunk)

            # 重新编号，保证跨块时句子序号连续
            valid_sentences: list[Sentence] = []
            for sentence in sentences:
                if not sentence.original.strip():
                    continue
                index_offset += 1
                sentence.index = index_offset
                valid_sentences.append(sentence)

            all_sentences.extend(valid_sentences)

            if translation.strip():
                translations.append(translation.strip())

        full_translation = "\n\n".join(translations)

        if self.client.config.debug:
            grammar_count = sum(len(s.grammar_points) for s in all_sentences)
            print(f"  [语法分析完成] {len(all_sentences)} 句，{grammar_count} 个语法点")

        return all_sentences, full_translation

    # ------------------------------------------------------------------
    # 单块分析
    # ------------------------------------------------------------------

    def _analyze_chunk(self, chunk: str) -> tuple[list[Sentence], str]:
        """分析一个文本块。"""
        prompt = GRAMMAR_ANALYSIS.format(text=chunk)

        try:
            data = self.client.chat_json(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
            )
        except LLMError as exc:
            print(f"  [警告] 语法分析失败：{exc}")
            return [], ""

        sentences = self._parse_sentences(data.get("sentences", []))
        translation = str(data.get("full_translation", ""))

        return sentences, translation

    def _parse_sentences(self, raw_sentences: list) -> list[Sentence]:
        """把模型返回的原始数据转成 Sentence 对象列表。"""
        result: list[Sentence] = []

        for item in raw_sentences:
            if not isinstance(item, dict):
                continue

            original = str(item.get("original", "")).strip()
            if not original:
                continue

            sentence = Sentence(
                index=int(item.get("index", 0) or 0),
                original=original,
                translation=str(item.get("translation", "")).strip(),
                structure=str(item.get("structure", "")).strip(),
            )

            # 解析语法点
            for gp_raw in item.get("grammar_points", []) or []:
                if not isinstance(gp_raw, dict):
                    continue

                gp_text = str(gp_raw.get("text", "")).strip()
                if not gp_text:
                    continue

                # ---- 关键校验：标注片段必须真实存在于原句中 ----
                # 模型经常改写片段（比如把 "be accustomed to" 写成 "be accustomed to doing"）
                # 这会导致界面无法正确高亮，所以必须校验
                if gp_text not in original:
                    if self.client.config.debug:
                        print(f"  [丢弃幻觉标注] 片段不在原句中：{gp_text!r}")
                    continue

                gp_type_raw = str(gp_raw.get("grammar_type", "")).strip()
                gp_type = (
                    GrammarType(gp_type_raw)
                    if gp_type_raw in self.VALID_TYPES
                    else GrammarType.COLLOCATION
                )

                sentence.grammar_points.append(
                    GrammarPoint(
                        text=gp_text,
                        grammar_type=gp_type,
                        subtype=str(gp_raw.get("subtype", "")).strip(),
                        explanation=str(gp_raw.get("explanation", "")).strip(),
                        signal_words=[
                            str(w).strip()
                            for w in (gp_raw.get("signal_words") or [])
                            if str(w).strip()
                        ],
                    )
                )

            # 按片段的出现位置排序，方便界面按顺序高亮
            sentence.grammar_points.sort(key=lambda g: original.find(g.text))

            result.append(sentence)

        return result

    # ------------------------------------------------------------------
    # 文本切分
    # ------------------------------------------------------------------

    @staticmethod
    def _split_text(text: str) -> list[str]:
        """按段落切分文本，控制单块长度不超限。

        策略：
        1. 先按空行分段
        2. 逐段累加，超过上限就切一块
        3. 单段本身超限时，按句子边界再切
        """
        text = text.strip()
        if len(text) <= MAX_CHUNK_CHARS:
            return [text]

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        # 如果按空行切不开（整段是一大坨），改按行切
        if len(paragraphs) == 1:
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for para in paragraphs:
            # 单段超限：先冲掉当前积累，再把这段按句子切开
            if len(para) > MAX_CHUNK_CHARS:
                if current:
                    chunks.append("\n\n".join(current))
                    current, current_len = [], 0

                for sub in GrammarAnalyzer._split_by_sentence(para):
                    chunks.append(sub)
                continue

            if current_len + len(para) > MAX_CHUNK_CHARS and current:
                chunks.append("\n\n".join(current))
                current, current_len = [], 0

            current.append(para)
            current_len += len(para)

        if current:
            chunks.append("\n\n".join(current))

        return chunks or [text]

    @staticmethod
    def _split_by_sentence(text: str, max_len: int = MAX_CHUNK_CHARS) -> list[str]:
        """按句子边界切分超长段落。

        用简单的规则识别句末标点：. ! ? 后面跟空格或结尾。
        注意要避开缩写（Mr. / U.S. / etc.）造成的误切。
        """
        import re

        # 在句末标点后切分，但排除常见缩写
        protected = text
        abbreviations = ["Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "St.", "etc.", "e.g.", "i.e.", "U.S.", "vs."]
        placeholders = {}
        for i, abbr in enumerate(abbreviations):
            token = f"\x00{i}\x00"
            placeholders[token] = abbr
            protected = protected.replace(abbr, token)

        parts = re.split(r"(?<=[.!?])\s+", protected)

        # 还原缩写
        restored: list[str] = []
        for part in parts:
            for token, abbr in placeholders.items():
                part = part.replace(token, abbr)
            if part.strip():
                restored.append(part.strip())

        # 累加句子，直到接近上限
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for sentence in restored:
            if current_len + len(sentence) > max_len and current:
                chunks.append(" ".join(current))
                current, current_len = [], 0
            current.append(sentence)
            current_len += len(sentence)

        if current:
            chunks.append(" ".join(current))

        return chunks or [text]
