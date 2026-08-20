"""文本提取模块：融合 PyMuPDF 文本层提取 + PaddleOCR

解决博文难点 5️⃣(乱码丢字) 和 6️⃣(段落碎片化)。
根据 detector 的判别结果，选择文本层提取或 OCR 提取。
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import fitz  # PyMuPDF
import numpy as np

from pdfgenie.detector import PageInfo
from pdfgenie.layout_analyzer import BlockType, LayoutBlock, PageLayout
from pdfgenie.utils import xy_cut_sort, clean_hyphenation

log = logging.getLogger(__name__)


@dataclass
class TextBlock:
    """提取到的文本块（段落级别）"""
    text: str
    bbox: Tuple[float, float, float, float]  # (x0, y0, x1, y1) PDF 坐标
    font_name: str = ""
    font_size: float = 12.0
    color: int = 0                           # RGB 颜色值
    block_type: BlockType = BlockType.TEXT
    is_translated: bool = False
    translated_text: str = ""
    line_count: int = 1                      # 原文行数

    @property
    def should_translate(self) -> bool:
        return self.block_type.should_translate and len(self.text.strip()) > 0


@dataclass
class PageContent:
    """单页提取结果"""
    page_no: int
    width: float
    height: float
    blocks: List[TextBlock] = field(default_factory=list)
    layout: Optional[PageLayout] = None

    @property
    def translatable_blocks(self) -> List[TextBlock]:
        return [b for b in self.blocks if b.should_translate]


class TextExtractor:
    """文本提取器：融合 PyMuPDF + PaddleOCR"""

    def __init__(self):
        self._ocr_engine = None

    def _init_ocr(self):
        """延迟初始化 PaddleOCR"""
        if self._ocr_engine is not None:
            return

        try:
            import os
            os.environ.setdefault('HUB_DATASET_ENDPOINT',
                                  'https://modelscope.cn/api/v1/datasets')
            from paddleocr import PaddleOCR
            log.info("初始化 PaddleOCR 引擎...")
            try:
                self._ocr_engine = PaddleOCR(lang="en")
            except Exception:
                self._ocr_engine = PaddleOCR()
            log.info("PaddleOCR 初始化完成")
        except ImportError:
            log.error("PaddleOCR 未安装，OCR 功能不可用")
            raise
        except Exception as e:
            log.error(f"PaddleOCR 初始化失败: {e}")
            raise

    def extract_page(self, doc: fitz.Document, page_no: int,
                     page_info: PageInfo,
                     layout: Optional[PageLayout] = None) -> PageContent:
        """提取单页内容"""
        page = doc[page_no]
        content = PageContent(
            page_no=page_no,
            width=page.rect.width,
            height=page.rect.height,
            layout=layout,
        )

        if page_info.needs_ocr:
            content.blocks = self._extract_with_ocr(page, layout)
        else:
            content.blocks = self._extract_with_text_layer(page, layout)

        log.info(f"Page {page_no}: 提取 {len(content.blocks)} 个文本块, "
                 f"{len(content.translatable_blocks)} 个需翻译")

        return content

    def _extract_with_text_layer(self, page: fitz.Page,
                                 layout: Optional[PageLayout] = None) -> List[TextBlock]:
        """从 PyMuPDF 文本层提取文本块

        策略: 保留 PyMuPDF 的 block/line 结构，而非重新合并 span。
        每个 PyMuPDF block 对应一个 TextBlock（段落级别）。
        """
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        blocks = []

        for block in text_dict.get("blocks", []):
            if block["type"] != 0:  # type 0 = 文本, 1 = 图片
                continue

            lines = block.get("lines", [])
            if not lines:
                continue

            # 收集整个 block 的文本
            block_text_parts = []
            font_sizes = []
            font_names = []
            colors = []

            for line in lines:
                line_text = ""
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if text:
                        line_text += text
                        font_sizes.append(span.get("size", 12.0))
                        font_names.append(span.get("font", ""))
                        colors.append(span.get("color", 0))
                if line_text.strip():
                    block_text_parts.append(line_text.strip())

            if not block_text_parts:
                continue

            full_text = clean_hyphenation(block_text_parts)
            bbox = tuple(block["bbox"])  # (x0, y0, x1, y1)

            # 使用最常见的字体和字号
            main_size = max(set(font_sizes), key=font_sizes.count) if font_sizes else 12.0
            main_font = max(set(font_names), key=font_names.count) if font_names else ""
            main_color = max(set(colors), key=colors.count) if colors else 0

            block_type = self._classify_block_by_layout(bbox, layout)

            blocks.append(TextBlock(
                text=full_text,
                bbox=bbox,
                font_name=main_font,
                font_size=main_size,
                color=main_color,
                block_type=block_type,
                line_count=len(block_text_parts),
            ))

        # XY-Cut 排序
        blocks = xy_cut_sort(blocks, key_func=lambda b: b.bbox)

        return blocks

    def _extract_with_ocr(self, page: fitz.Page,
                          layout: Optional[PageLayout] = None) -> List[TextBlock]:
        """使用 PaddleOCR 从页面图片提取文本"""
        self._init_ocr()

        # 自适应分辨率：长边限制在 1600~2000px，既保证 OCR 精度又大幅提高速度
        page_max = max(page.rect.width, page.rect.height)
        scale = min(2.0, 1800.0 / max(page_max, 1.0))
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat)

        image = np.frombuffer(pix.samples, np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        if pix.n == 4:
            image = image[:, :, :3]

        # PaddleOCR 识别
        try:
            result = self._ocr_engine.predict(image)
        except Exception:
            result = self._ocr_engine.ocr(image)

        if not result:
            log.warning(f"Page {page.number}: OCR 未识别到文字")
            return []

        # 坐标转换 (图片像素 → PDF 点)
        scale_x = page.rect.width / pix.width
        scale_y = page.rect.height / pix.height

        ocr_lines = []

        # 解析 PaddleOCR 3.x (PaddleX OCRResult 格式)
        res_item = result[0]
        if hasattr(res_item, 'keys') and 'rec_texts' in res_item:
            texts = res_item.get('rec_texts', [])
            scores = res_item.get('rec_scores', [])
            boxes = res_item.get('rec_boxes', [])
            polys = res_item.get('rec_polys', [])

            for i, text in enumerate(texts):
                if not text or not text.strip():
                    continue
                score = scores[i] if i < len(scores) else 1.0
                if score < 0.4:
                    continue

                if i < len(boxes) and len(boxes[i]) == 4:
                    bx0, by0, bx1, by1 = boxes[i]
                elif i < len(polys) and len(polys[i]) >= 4:
                    poly = polys[i]
                    bx0 = min(p[0] for p in poly)
                    by0 = min(p[1] for p in poly)
                    bx1 = max(p[0] for p in poly)
                    by1 = max(p[1] for p in poly)
                else:
                    continue

                bbox = (
                    bx0 * scale_x,
                    by0 * scale_y,
                    bx1 * scale_x,
                    by1 * scale_y,
                )
                height = max(bbox[3] - bbox[1], 4.0)
                font_size = height * 0.75

                ocr_lines.append({
                    'text': text.strip(),
                    'bbox': bbox,
                    'size': font_size,
                })

        # 解析 PaddleOCR 2.x (列表格式 [[box, (text, score)]])
        elif isinstance(res_item, list):
            for line in res_item:
                if not line or len(line) < 2:
                    continue
                box_points = line[0]
                text_info = line[1]
                if isinstance(text_info, (tuple, list)):
                    text = text_info[0]
                    confidence = text_info[1] if len(text_info) > 1 else 1.0
                else:
                    text = str(text_info)
                    confidence = 1.0

                if confidence < 0.4 or not text.strip():
                    continue

                xs = [p[0] for p in box_points]
                ys = [p[1] for p in box_points]
                bbox = (
                    min(xs) * scale_x,
                    min(ys) * scale_y,
                    max(xs) * scale_x,
                    max(ys) * scale_y,
                )

                height = max(bbox[3] - bbox[1], 4.0)
                font_size = height * 0.75

                ocr_lines.append({
                    'text': text.strip(),
                    'bbox': bbox,
                    'size': font_size,
                })

        # 按行合并为段落：相邻行且左对齐相近 → 同一段落
        blocks = self._merge_ocr_lines_to_paragraphs(ocr_lines)

        # 分类
        for block in blocks:
            block.block_type = self._classify_block_by_layout(block.bbox, layout)

        blocks = xy_cut_sort(blocks, key_func=lambda b: b.bbox)
        return blocks

    def _merge_ocr_lines_to_paragraphs(self, lines: list) -> List[TextBlock]:
        """将 OCR 行合并为段落

        合并条件:
        - 垂直距离 < 行高 * 1.5
        - 左边界对齐（差异 < 字号 * 2）
        """
        if not lines:
            return []

        lines = sorted(lines, key=lambda l: (l['bbox'][1], l['bbox'][0]))

        paragraphs = []
        current_lines = [lines[0]]

        for line in lines[1:]:
            prev = current_lines[-1]
            prev_bottom = prev['bbox'][3]
            curr_top = line['bbox'][1]
            gap = curr_top - prev_bottom
            size = max(prev['size'], line['size'])

            # 合并条件
            if (gap < size * 1.5 and
                    abs(line['bbox'][0] - current_lines[0]['bbox'][0]) < size * 3):
                current_lines.append(line)
            else:
                paragraphs.append(self._lines_to_block(current_lines))
                current_lines = [line]

        paragraphs.append(self._lines_to_block(current_lines))
        return paragraphs

    def _lines_to_block(self, lines: list) -> TextBlock:
        """将多行合并为一个 TextBlock"""
        text = clean_hyphenation([l['text'] for l in lines])
        from pdfgenie.utils import merge_bboxes
        bbox = merge_bboxes([l['bbox'] for l in lines])
        size = max(l['size'] for l in lines)
        return TextBlock(
            text=text,
            bbox=bbox,
            font_name="OCR",
            font_size=size,
            line_count=len(lines),
        )

    def _classify_block_by_layout(self, bbox: tuple,
                                  layout: Optional[PageLayout] = None) -> BlockType:
        """根据版面分析结果给文本块分类"""
        if layout is None:
            return BlockType.TEXT

        best_match = None
        best_overlap = 0

        for lb in layout.blocks:
            from pdfgenie.utils import bbox_overlap
            overlap = bbox_overlap(bbox, lb.bbox)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = lb

        if best_match and best_overlap > 0:
            return best_match.block_type

        return BlockType.TEXT
