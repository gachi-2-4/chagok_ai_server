from dataclasses import dataclass, field


@dataclass
class OcrBlock:
    text: str
    bbox: list[float]  # [x, y, w, h] 페이지 기준 0~1 비율
    confidence: float = 1.0


@dataclass
class OcrPage:
    page: int  # 1부터
    text: str
    blocks: list[OcrBlock] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        if not self.blocks:
            return 1.0 if self.text.strip() else 0.0
        return sum(b.confidence for b in self.blocks) / len(self.blocks)


@dataclass
class OcrDocument:
    pages: list[OcrPage]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def full_text(self) -> str:
        return "\n".join(f"[{p.page}페이지]\n{p.text.strip()}" for p in self.pages)

    @property
    def is_empty(self) -> bool:
        return not any(p.text.strip() for p in self.pages)

    def page(self, number: int) -> OcrPage | None:
        return next((p for p in self.pages if p.page == number), None)
