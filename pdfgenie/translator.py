"""翻译引擎模块：支持 Google / OpenAI / Ollama / LMStudio

参考 pdf2zh 的 BaseTranslator 抽象，支持多引擎切换。
- 以段落为最小翻译单位（非按行），解决博文难点 6️⃣
- 翻译缓存集成
- 多线程并发翻译 + 失败重试
- 公式标记保护
"""

import concurrent.futures
import html
import logging
import os
import re
from typing import List

import requests
from tenacity import retry, stop_after_attempt, wait_fixed

from pdfgenie import cache
from pdfgenie.utils import remove_control_characters, protect_formulas, restore_formulas

log = logging.getLogger(__name__)


class BaseTranslator:
    """翻译器基类"""

    def __init__(self, service: str, lang_out: str = "zh-CN",
                 lang_in: str = "en", model: str = ""):
        self.service = service
        self.lang_out = lang_out
        self.lang_in = lang_in
        self.model = model

    def translate(self, text: str) -> str:
        raise NotImplementedError

    def __str__(self):
        return f"{self.service}({self.lang_in}→{self.lang_out})"


class GoogleTranslator(BaseTranslator):
    """免费 Google 翻译（默认引擎）"""

    def __init__(self, lang_out: str = "zh-CN", lang_in: str = "en", **kwargs):
        super().__init__("google", lang_out, lang_in)
        self.session = requests.Session()
        self.base_url = "http://translate.google.com/m"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }

    def translate(self, text: str) -> str:
        text = text[:5000]  # Google Translate 限制
        response = self.session.get(
            self.base_url,
            params={"tl": self.lang_out, "sl": self.lang_in, "q": text},
            headers=self.headers,
            timeout=30,
        )
        if response.status_code == 400:
            return "TRANSLATION_ERROR"

        results = re.findall(
            r'(?s)class="(?:t0|result-container)">(.*?)<', response.text
        )
        if not results:
            raise ValueError("Empty translation result from Google")

        return html.unescape(results[0])


class OpenAITranslator(BaseTranslator):
    """OpenAI / 兼容 API 翻译"""

    def __init__(self, lang_out: str = "zh-CN", lang_in: str = "en",
                 model: str = "gpt-4o-mini", **kwargs):
        super().__init__("openai", lang_out, lang_in, model)
        import openai
        self.client = openai.OpenAI()  # 使用 OPENAI_API_KEY 和 OPENAI_BASE_URL 环境变量
        self.options = {"temperature": 0}

    def translate(self, text: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model or "gpt-4o-mini",
            **self.options,
            messages=[
                {
                    "role": "system",
                    "content": "You are a professional, authentic machine translation engine.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Translate the following source text to {self.lang_out}. "
                        f"Keep formula notation and placeholders like __MATH_0__ unchanged. "
                        f"Output translation directly without any additional text.\n"
                        f"Source Text: {text}\n"
                        f"Translated Text:"
                    ),
                },
            ],
        )
        return response.choices[0].message.content.strip()


class OllamaTranslator(BaseTranslator):
    """Ollama 本地 LLM 翻译"""

    def __init__(self, lang_out: str = "zh-CN", lang_in: str = "en",
                 model: str = "", **kwargs):
        super().__init__("ollama", lang_out, lang_in, model)
        import ollama
        self.client = ollama.Client()
        self.options = {"temperature": 0}
        self.resolved_model = self._resolve_model()
        log.info(f"Ollama 连接成功, 模型: {self.resolved_model}")

    def _resolve_model(self) -> str:
        if self.model:
            return self.model
        try:
            models_res = self.client.list()
            if models_res and hasattr(models_res, 'models') and models_res.models:
                return models_res.models[0].model
        except Exception:
            pass
        return "qwen2.5:7b"

    def translate(self, text: str) -> str:
        response = self.client.chat(
            model=self.resolved_model,
            options=self.options,
            messages=[
                {
                    "role": "system",
                    "content": "You are a professional, authentic machine translation engine.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Translate the following source text to {self.lang_out}. "
                        f"Keep formula notation and placeholders like __MATH_0__ unchanged. "
                        f"Output translation directly without any additional text.\n"
                        f"Source Text: {text}\n"
                        f"Translated Text:"
                    ),
                },
            ],
        )
        return response["message"]["content"].strip()


class LMStudioTranslator(BaseTranslator):
    """LMStudio 本地 LLM 翻译（OpenAI 兼容 API）

    LMStudio 默认在 http://localhost:1234/v1 提供 OpenAI 兼容接口。
    可通过 LMSTUDIO_BASE_URL 环境变量修改地址。
    """

    def __init__(self, lang_out: str = "zh-CN", lang_in: str = "en",
                 model: str = "", **kwargs):
        super().__init__("lmstudio", lang_out, lang_in, model)
        import openai
        base_url = os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
        self.client = openai.OpenAI(
            base_url=base_url,
            api_key="lm-studio",  # LMStudio 不需要真实 key
        )
        self.options = {"temperature": 0}
        self.resolved_model = self._resolve_model()
        log.info(f"LMStudio 连接: {base_url}, 模型: {self.resolved_model}")

    def _resolve_model(self) -> str:
        if self.model:
            return self.model
        try:
            models = self.client.models.list()
            if models.data:
                return models.data[0].id
        except Exception:
            pass
        return "default"

    def translate(self, text: str) -> str:
        response = self.client.chat.completions.create(
            model=self.resolved_model,
            **self.options,
            messages=[
                {
                    "role": "system",
                    "content": "You are a professional, authentic machine translation engine.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Translate the following source text to {self.lang_out}. "
                        f"Keep formula notation and placeholders like __MATH_0__ unchanged. "
                        f"Output translation directly without any additional text.\n"
                        f"Source Text: {text}\n"
                        f"Translated Text:"
                    ),
                },
            ],
        )
        return response.choices[0].message.content.strip()


# ── 翻译引擎工厂 ──────────────────────────────────────────────────────

TRANSLATORS = {
    "google": GoogleTranslator,
    "openai": OpenAITranslator,
    "ollama": OllamaTranslator,
    "lmstudio": LMStudioTranslator,
}


def create_translator(service: str = "google", lang_out: str = "zh-CN",
                      lang_in: str = "en", model: str = "") -> BaseTranslator:
    """创建翻译器实例"""
    cls = TRANSLATORS.get(service)
    if cls is None:
        raise ValueError(f"Unknown translator service: {service}. "
                         f"Available: {list(TRANSLATORS.keys())}")
    return cls(lang_out=lang_out, lang_in=lang_in, model=model)


# ── 批量翻译 ─────────────────────────────────────────────────────────

def translate_blocks(blocks: list, translator: BaseTranslator,
                     max_workers: int = 4) -> list:
    """并发翻译多个文本块，带缓存、公式保护和重试

    Args:
        blocks: TextBlock 列表（原地修改 translated_text 和 is_translated）
        translator: 翻译器
        max_workers: 并发线程数
    Returns:
        翻译后的 blocks
    """
    translatable = [b for b in blocks if b.should_translate]

    if not translatable:
        return blocks

    @retry(wait=wait_fixed(2), stop=stop_after_attempt(3))
    def _translate_one(block):
        raw_text = block.text.strip()
        if not raw_text:
            return

        # 查缓存（以未保护的原文为 key）
        key = cache.cache_key(raw_text, str(translator), translator.lang_in, translator.lang_out)
        cached = cache.load_translation(key)
        if cached:
            block.translated_text = cached
            block.is_translated = True
            return

        # 保护公式
        protected_text, formulas = protect_formulas(raw_text)

        try:
            result = translator.translate(protected_text)
            if formulas:
                result = restore_formulas(result, formulas)
            result = remove_control_characters(result)
            block.translated_text = result
            block.is_translated = True

            # 写缓存
            cache.save_translation(
                key, raw_text, result,
                str(translator), translator.lang_in, translator.lang_out
            )
        except Exception as e:
            log.error(f"翻译失败: {raw_text[:50]}... → {e}")
            raise

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_translate_one, b): b for b in translatable}
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                block = futures[future]
                log.error(f"翻译最终失败: {block.text[:50]}...")
                block.translated_text = block.text  # 回退原文
                block.is_translated = False

    translated_count = sum(1 for b in translatable if b.is_translated)
    log.info(f"翻译完成: {translated_count}/{len(translatable)} 个文本块")

    return blocks

