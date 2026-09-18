"""
单词精讲模块。

职责：给定单词，生成包含词根词缀、词源、分级释义、搭配例句的完整词卡。

关键设计：
1. **两级筛选**：先从整段文本中挑出值得讲的词（KEYWORD_EXTRACTION），
   再对每个词单独生成词卡。不是每个词都值得花钱讲。
2. **并发请求**：多个单词的词卡可以并行生成，显著缩短等待时间。
3. **本地缓存**：同一个单词只查一次，结果存 SQLite，后续命中直接返回。
   这是"混合方案"里本地那一半的落点——省钱且提速。
"""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.llm_client import LLMClient, LLMError
from src.models import Collocation, WordEntry, WordForm
from src.prompts import KEYWORD_EXTRACTION, WORD_DETAIL


class WordCache:
    """单词词卡本地缓存。

    用 SQLite 存储，键是单词（小写），值是词卡的 JSON。
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else self._default_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @staticmethod
    def _default_path() -> Path:
        from src.config import get_config

        return get_config().data_dir / "cache" / "words.db"

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS word_cache (
                    word       TEXT PRIMARY KEY,
                    payload    TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
                """
            )
            conn.commit()

    def get(self, word: str) -> dict | None:
        """读取缓存。未命中返回 None。"""
        key = word.strip().lower()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT payload FROM word_cache WHERE word = ?", (key,)
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def set(self, word: str, payload: dict) -> None:
        """写入缓存。"""
        key = word.strip().lower()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO word_cache (word, payload)
                VALUES (?, ?)
                ON CONFLICT(word) DO UPDATE SET
                    payload = excluded.payload,
                    created_at = datetime('now', 'localtime')
                """,
                (key, json.dumps(payload, ensure_ascii=False)),
            )
            conn.commit()

    def clear(self) -> int:
        """清空缓存，返回删除条数。"""
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM word_cache").fetchone()[0]
            conn.execute("DELETE FROM word_cache")
            conn.commit()
        return count


class VocabularyTutor:
    """单词精讲器。"""

    def __init__(
        self,
        client: LLMClient | None = None,
        cache: WordCache | None = None,
        max_workers: int = 4,
    ):
        self.client = client or LLMClient()
        self.cache = cache if cache is not None else WordCache()
        self.max_workers = max_workers

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def process(self, text: str, count: int = 8) -> list[WordEntry]:
        """从文本中挑词并生成词卡。

        Args:
            text: 英文文本
            count: 挑选的单词数量

        Returns:
            词卡列表
        """
        words = self.select_keywords(text, count=count)
        if not words:
            return []

        return self.build_cards(words, text)

    def select_keywords(self, text: str, count: int = 8) -> list[str]:
        """从文本中挑选值得精讲的单词。"""
        if self.client.config.debug:
            print(f"  [选词] 从文本中挑选 {count} 个重点词...")

        prompt = KEYWORD_EXTRACTION.format(count=count, text=text)

        try:
            data = self.client.chat_json(
                [{"role": "user", "content": prompt}], temperature=0.2
            )
        except LLMError as exc:
            print(f"  [警告] 选词失败：{exc}")
            return []

        raw_words = data.get("words", [])

        # 如果模型返回的是 {"items": [...]} 形式，兼容处理
        if not raw_words and isinstance(data.get("items"), list):
            raw_words = data["items"]

        words: list[str] = []
        seen: set[str] = set()

        for w in raw_words:
            w = str(w).strip().lower()
            # 只保留纯字母词（允许连字符）
            if not w or not all(c.isalpha() or c in "-'" for c in w):
                continue
            if w in seen:
                continue
            seen.add(w)
            words.append(w)

        if self.client.config.debug:
            print(f"  [选词完成] {', '.join(words)}")

        return words[:count]

    def build_cards(self, words: list[str], context_text: str = "") -> list[WordEntry]:
        """为多个单词生成词卡（并发）。"""
        if not words:
            return []

        cards: dict[str, WordEntry] = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.build_card, word, context_text): word
                for word in words
            }

            for future in as_completed(futures):
                word = futures[future]
                try:
                    card = future.result()
                    if card:
                        cards[word] = card
                except Exception as exc:  # noqa: BLE001
                    print(f"  [警告] 生成 {word} 词卡失败：{exc}")

        # 按传入顺序返回，保证输出稳定
        return [cards[w] for w in words if w in cards]

    def build_card(self, word: str, context_text: str = "") -> WordEntry | None:
        """生成单个单词的词卡。命中缓存则直接返回。"""
        # ---- 查缓存 ----
        cached = self.cache.get(word)
        if cached:
            if self.client.config.debug:
                print(f"  [缓存命中] {word}")
            return self._dict_to_entry(cached)

        if self.client.config.debug:
            print(f"  [生成词卡] {word}")

        # ---- 构造上下文提示 ----
        context_hint = ""
        if context_text:
            sentence = self._find_context_sentence(word, context_text)
            if sentence:
                context_hint = (
                    f"该词在本文中出现的句子是：\n「{sentence}」\n"
                    f"请特别注明它在这个句子里的具体含义。"
                )

        prompt = WORD_DETAIL.format(word=word, context_hint=context_hint)

        try:
            data = self.client.chat_json(
                [{"role": "user", "content": prompt}], temperature=0.2
            )
        except LLMError as exc:
            print(f"  [警告] {word} 词卡生成失败：{exc}")
            return None

        # 写缓存
        self.cache.set(word, data)

        entry = self._dict_to_entry(data)

        # 补充上下文句子
        if context_text and not entry.context_sentence:
            entry.context_sentence = self._find_context_sentence(word, context_text)

        return entry

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _find_context_sentence(word: str, text: str) -> str:
        """在原文中找到包含该词的句子。"""
        import re

        stem = word.lower().rstrip("e")
        pattern = re.compile(rf"\b{re.escape(stem)}\w*\b", re.IGNORECASE)

        # 按句末标点切句，保留标点
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for sentence in sentences:
            if pattern.search(sentence):
                return sentence.strip()
        return ""

    @staticmethod
    def _dict_to_entry(data: dict) -> WordEntry:
        """把模型返回的字典转为 WordEntry 对象。"""
        entry = WordEntry(
            word=str(data.get("word", "")).strip(),
            phonetic_uk=str(data.get("phonetic_uk", "")).strip(),
            phonetic_us=str(data.get("phonetic_us", "")).strip(),
            morphology=str(data.get("morphology", "")).strip(),
            etymon=str(data.get("etymon", "")).strip(),
            notes=str(data.get("notes", "")).strip(),
        )

        # 释义
        for form_raw in data.get("all_definitions", []) or []:
            if not isinstance(form_raw, dict):
                continue
            pos = str(form_raw.get("part_of_speech", "")).strip()
            defs = [
                str(d).strip()
                for d in (form_raw.get("definitions") or [])
                if str(d).strip()
            ]
            if pos or defs:
                entry.all_definitions.append(
                    WordForm(part_of_speech=pos, definitions=defs)
                )

        # 高频义项
        entry.high_freq_definitions = [
            str(d).strip()
            for d in (data.get("high_freq_definitions") or [])
            if str(d).strip()
        ]

        # 兜底：模型没给高频义项时，从完整释义里取前 3 条
        if not entry.high_freq_definitions and entry.all_definitions:
            flat: list[str] = []
            for form in entry.all_definitions:
                for d in form.definitions:
                    label = f"{form.part_of_speech} {d}".strip()
                    flat.append(label)
                    if len(flat) >= 3:
                        break
                if len(flat) >= 3:
                    break
            entry.high_freq_definitions = flat

        # 搭配
        for col_raw in data.get("collocations", []) or []:
            if not isinstance(col_raw, dict):
                continue
            phrase = str(col_raw.get("phrase", "")).strip()
            if not phrase:
                continue
            entry.collocations.append(
                Collocation(
                    phrase=phrase,
                    meaning=str(col_raw.get("meaning", "")).strip(),
                    example=str(col_raw.get("example", "")).strip(),
                    example_translation=str(
                        col_raw.get("example_translation", "")
                    ).strip(),
                )
            )

        return entry
