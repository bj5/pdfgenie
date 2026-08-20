"""格式回填模块：支持双语对照（逐页）+ 纯译文两种模式

模式:
- bilingual (默认): 复制原页面布局及图片，每页原文后插入一页译文，原文页完全不动
- mono: 原文移除，仅显示译文
"""

import logging
import math
from typing import List

import fitz  # PyMuPDF

from pdfgenie.extractor import PageContent, TextBlock
from pdfgenie.layout_analyzer import BlockType
from pdfgenie.utils import int_to_rgb

log = logging.getLogger(__name__)

# CJK 行距系数，参考 pdf2zh
LANG_LINE_SPACING = {
    "zh-CN": 1.3,
    "zh-TW": 1.3,
    "ja": 1.15,
    "ko": 1.2,
    "en": 1.2,
}


class PDFRenderer:
    """PDF 格式回填渲染器"""

    def __init__(self, lang_out: str = "zh-CN", bilingual: bool = True):
        """
        Args:
            lang_out: 目标语言
            bilingual: True=双语对照(逐页), False=纯译文
        """
        self.lang_out = lang_out
        self.bilingual = bilingual
        self.line_spacing = LANG_LINE_SPACING.get(lang_out, 1.3)

    def render(self, doc: fitz.Document, pages_content: List[PageContent],
               output_path: str) -> str:
        """将翻译结果渲染回 PDF"""
        mode_str = "双语对照(逐页)" if self.bilingual else "纯译文"
        log.info(f"开始渲染 ({mode_str}): {output_path}")

        if self.bilingual:
            self._render_bilingual(doc, pages_content)
        else:
            self._render_mono(doc, pages_content)

        doc.save(output_path, garbage=4, deflate=True)
        log.info(f"PDF 保存完成: {output_path}")
        return output_path

    def _get_cjk_fontname(self) -> str:
        """获取 CJK 字体名"""
        lang_font_map = {
            "zh-CN": "china-ss",
            "zh-TW": "china-ts",
            "ja": "japan-s",
            "ko": "korea-s",
        }
        return lang_font_map.get(self.lang_out, "china-ss")

    # ──────────────────────────────────────────────────────────────────
    # 双语对照模式（逐页）
    # ──────────────────────────────────────────────────────────────────

    def _render_bilingual(self, doc: fitz.Document,
                          pages_content: List[PageContent]) -> None:
        """双语对照渲染：原文页不动，每页后面插入一页克隆译文页

        通过 doc.fullcopy_page 复制原页面，完整保留背景图片、图表、线条及版式布局。
        在克隆页上通过 Redaction 清除原文字层，并填入对应位置的译文。

        最终 PDF 结构:
          原文第1页 → 译文第1页 → 原文第2页 → 译文第2页 → ...
        """
        fontname = self._get_cjk_fontname()

        # 倒序处理，这样插入新页时不会打乱前面的页码
        for page_content in reversed(pages_content):
            translated_blocks = [b for b in page_content.blocks
                                 if b.is_translated and b.translated_text.strip()]

            if not translated_blocks:
                log.debug(f"Page {page_content.page_no}: 无译文，跳过")
                continue

            orig_pno = page_content.page_no
            target = -1 if orig_pno == len(doc) - 1 else orig_pno + 1

            # 克隆原页到目标位置
            doc.fullcopy_page(orig_pno, to=target)
            new_pno = orig_pno + 1
            new_page = doc[new_pno]

            log.info(f"Page {orig_pno}: 插入克隆译文页 (新页号: {new_pno}, "
                     f"{len(translated_blocks)} 个文本块)")

            # 在克隆页上：通过 Redaction 清除原文字层，保留背景图像/图表/线框
            for block in translated_blocks:
                new_page.add_redact_annot(fitz.Rect(block.bbox), fill=(1, 1, 1))
            new_page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

            # 在克隆页上渲染所有译文（使用与原文完全相同的坐标和排版区域）
            for block in translated_blocks:
                self._render_block_on_page(new_page, block, fontname)

    # ──────────────────────────────────────────────────────────────────
    # 纯译文模式
    # ──────────────────────────────────────────────────────────────────

    def _render_mono(self, doc: fitz.Document,
                     pages_content: List[PageContent]) -> None:
        """纯译文渲染：Redaction 移除原文，替换为译文"""
        fontname = self._get_cjk_fontname()

        for page_content in pages_content:
            translated_blocks = [b for b in page_content.blocks
                                 if b.is_translated and b.translated_text.strip()]

            if not translated_blocks:
                continue

            page = doc[page_content.page_no]
            log.info(f"Page {page_content.page_no}: 纯译文渲染 "
                     f"{len(translated_blocks)} 个文本块")

            # Redaction 移除原文
            for block in translated_blocks:
                page.add_redact_annot(fitz.Rect(block.bbox), fill=(1, 1, 1))
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

            # 插入译文
            for block in translated_blocks:
                self._render_block_on_page(page, block, fontname)

    # ──────────────────────────────────────────────────────────────────
    # 文本块渲染与自适应排版
    # ──────────────────────────────────────────────────────────────────

    def _render_block_on_page(self, page: fitz.Page, block: TextBlock,
                              fontname: str) -> None:
        """在页面上渲染单个译文块

        使用原文相同的 bbox 坐标，自适应字号与行距，保持视觉风格统一。
        """
        rect = fitz.Rect(block.bbox)
        translated = block.translated_text.strip()
        if not translated:
            return

        # 字体颜色
        color = int_to_rgb(block.color)

        # 字号：以原文字号为基准，按区域长宽和字数自适应计算
        font_size = self._fit_font_size(translated, block.font_size, rect)

        try:
            rc = page.insert_textbox(
                rect,
                translated,
                fontsize=font_size,
                fontname=fontname,
                color=color,
                align=fitz.TEXT_ALIGN_LEFT,
            )

            # 溢出时自适应缩小字号尝试
            attempts = 0
            while rc < 0 and attempts < 10 and font_size > 3.5:
                font_size *= 0.88
                rc = page.insert_textbox(
                    rect,
                    translated,
                    fontsize=font_size,
                    fontname=fontname,
                    color=color,
                    align=fitz.TEXT_ALIGN_LEFT,
                )
                attempts += 1

            if rc < 0:
                log.debug(f"译文略有溢出，降级逐行绘制: {translated[:30]}...")
                self._insert_line_by_line(page, rect, translated,
                                          font_size, fontname, color)

        except Exception as e:
            log.error(f"插入译文异常: {e}")
            self._insert_line_by_line(page, rect, translated,
                                      font_size, fontname, color)

    def _insert_line_by_line(self, page: fitz.Page, rect: fitz.Rect,
                             text: str, font_size: float,
                             fontname: str,
                             color: tuple = (0, 0, 0)) -> None:
        """降级方案：逐行插入文本"""
        y = rect.y0 + font_size
        chars_per_line = max(1, int(rect.width / max(font_size * 0.7, 1.0)))

        pos = 0
        while pos < len(text) and y < rect.y1:
            line = text[pos:pos + chars_per_line]
            try:
                page.insert_text(
                    (rect.x0, y), line,
                    fontsize=font_size, fontname=fontname, color=color,
                )
            except Exception:
                break
            y += font_size * self.line_spacing
            pos += chars_per_line

    def _char_width_units(self, text: str) -> float:
        """估算字符宽度单位总和（CJK 全角=1.0, 标点/半角英数≈0.55）"""
        total = 0.0
        for ch in text:
            if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' or \
               '\uff00' <= ch <= '\uffef':
                total += 1.0
            else:
                total += 0.55
        return total

    def _fit_font_size(self, text: str, original_size: float,
                       rect: fitz.Rect) -> float:
        """根据文本长度和目标矩形 bbox 智能计算最佳字号"""
        font_size = min(max(original_size, 7.0), 48.0)
        box_width = max(rect.width, 8.0)
        box_height = max(rect.height, 8.0)

        if not text:
            return font_size

        cw = self._char_width_units(text)

        for _ in range(25):
            chars_per_line = max(1.0, box_width / font_size)
            lines = max(1.0, math.ceil(cw / chars_per_line))
            height_needed = (lines - 1) * font_size * self.line_spacing + font_size
            if height_needed <= box_height and (cw <= chars_per_line or lines * font_size <= box_height):
                break
            font_size *= 0.92

        return max(round(font_size, 1), 3.5)
