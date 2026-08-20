"""版面分析模块：使用 PaddleOCR PP-Structure 进行版面区域检测

解决博文难点 2️⃣(版面混乱)、3️⃣(表格结构)、4️⃣(图片中文本)。
区分正文、标题、表格、图片、公式等区域，指导后续翻译策略。
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)


class BlockType(Enum):
    """版面区域类型"""
    TEXT = "text"           # 正文文本 → 翻译
    TITLE = "title"         # 标题 → 翻译
    TABLE = "table"         # 表格 → 逐单元格翻译
    FIGURE = "figure"       # 图片 → 保留
    EQUATION = "equation"   # 公式 → 保留不翻译
    HEADER = "header"       # 页眉 → 可选翻译
    FOOTER = "footer"       # 页脚 → 可选翻译
    REFERENCE = "reference" # 参考文献 → 可选翻译
    CAPTION = "caption"     # 图注/表注 → 翻译

    @property
    def should_translate(self) -> bool:
        return self in (BlockType.TEXT, BlockType.TITLE, BlockType.CAPTION,
                        BlockType.HEADER, BlockType.FOOTER, BlockType.REFERENCE)


@dataclass
class LayoutBlock:
    """版面分析检测到的单个区域"""
    bbox: Tuple[float, float, float, float]  # (x0, y0, x1, y1) 页面坐标
    block_type: BlockType
    confidence: float = 1.0
    text: str = ""             # 提取到的文字（如有）
    children: list = field(default_factory=list)  # 子区域（如表格单元格）

    @property
    def area(self) -> float:
        return max(0, self.bbox[2] - self.bbox[0]) * max(0, self.bbox[3] - self.bbox[1])


@dataclass
class PageLayout:
    """单页版面分析结果"""
    page_no: int
    width: float
    height: float
    blocks: List[LayoutBlock] = field(default_factory=list)

    @property
    def text_blocks(self) -> List[LayoutBlock]:
        return [b for b in self.blocks if b.block_type.should_translate]

    @property
    def preserve_blocks(self) -> List[LayoutBlock]:
        return [b for b in self.blocks if not b.block_type.should_translate]


class LayoutAnalyzer:
    """使用 PaddleOCR PP-Structure 进行版面分析"""

    def __init__(self):
        self._engine = None

    def _init_engine(self):
        """延迟初始化 PP-Structure 引擎"""
        if self._engine is not None:
            return

        try:
            from paddleocr import PPStructure
            log.info("初始化 PP-Structure 版面分析引擎...")
            try:
                self._engine = PPStructure(table=True, ocr=True, layout=True, lang="en")
            except Exception:
                try:
                    self._engine = PPStructure()
                except Exception as e2:
                    log.warning(f"PP-Structure 参数不匹配: {e2}，降级为基础版面分析")
                    self._engine = None
            if self._engine:
                log.info("PP-Structure 初始化完成")
        except (ImportError, Exception) as e:
            log.warning(f"PP-Structure 不可用: {e}，降级为基础版面分析")
            self._engine = None

    def analyze(self, image: np.ndarray, page_no: int = 0,
                page_width: float = 0, page_height: float = 0) -> PageLayout:
        """分析页面图片的版面结构

        Args:
            image: BGR numpy 图片数组
            page_no: 页码
            page_width: PDF 页面宽度（点）
            page_height: PDF 页面高度（点）
        Returns:
            PageLayout
        """
        self._init_engine()

        layout = PageLayout(
            page_no=page_no,
            width=page_width or image.shape[1],
            height=page_height or image.shape[0],
        )

        if self._engine is None:
            # 降级：整页作为一个文本块
            layout.blocks.append(LayoutBlock(
                bbox=(0, 0, layout.width, layout.height),
                block_type=BlockType.TEXT,
            ))
            return layout

        try:
            result = self._engine(image)
            img_h, img_w = image.shape[:2]
            # 坐标缩放比例（图片坐标 → PDF 点坐标）
            scale_x = page_width / img_w if page_width else 1.0
            scale_y = page_height / img_h if page_height else 1.0

            for item in result:
                block_type = self._map_type(item.get('type', 'text'))
                bbox_raw = item.get('bbox', [0, 0, img_w, img_h])

                # 坐标转换
                bbox = (
                    bbox_raw[0] * scale_x,
                    bbox_raw[1] * scale_y,
                    bbox_raw[2] * scale_x,
                    bbox_raw[3] * scale_y,
                )

                block = LayoutBlock(
                    bbox=bbox,
                    block_type=block_type,
                    confidence=item.get('score', 1.0),
                )

                # 提取 OCR 文本
                if 'res' in item:
                    if isinstance(item['res'], list):
                        texts = []
                        for line in item['res']:
                            if isinstance(line, dict) and 'text' in line:
                                texts.append(line['text'])
                            elif isinstance(line, (list, tuple)) and len(line) >= 2:
                                texts.append(str(line[1][0]) if isinstance(line[1], (list, tuple)) else str(line[1]))
                        block.text = ' '.join(texts)
                    elif isinstance(item['res'], dict) and 'html' in item['res']:
                        # 表格结果为 HTML
                        block.text = item['res']['html']

                layout.blocks.append(block)

            log.info(f"Page {page_no}: 检测到 {len(layout.blocks)} 个区域 "
                     f"({len(layout.text_blocks)} 文本, {len(layout.preserve_blocks)} 保留)")

        except Exception as e:
            log.error(f"PP-Structure 分析 Page {page_no} 失败: {e}")
            # 降级
            layout.blocks.append(LayoutBlock(
                bbox=(0, 0, layout.width, layout.height),
                block_type=BlockType.TEXT,
            ))

        return layout

    @staticmethod
    def _map_type(pp_type: str) -> BlockType:
        """将 PP-Structure 的类型映射到 BlockType"""
        mapping = {
            'text': BlockType.TEXT,
            'title': BlockType.TITLE,
            'table': BlockType.TABLE,
            'figure': BlockType.FIGURE,
            'equation': BlockType.EQUATION,
            'header': BlockType.HEADER,
            'footer': BlockType.FOOTER,
            'reference': BlockType.REFERENCE,
            'table_caption': BlockType.CAPTION,
            'figure_caption': BlockType.CAPTION,
        }
        return mapping.get(pp_type.lower(), BlockType.TEXT)
