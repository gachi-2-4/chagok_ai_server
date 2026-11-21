"""법령 검색 파일(data/law_index.npz) 기반 검색. DB 없이 메모리에서 검색합니다 (작은 서버용).

파일은 scripts/export_law_index.py 로 법령 DB에서 만듭니다.
검색 방식은 DB와 같습니다: 분야 태그로 거른 뒤 벡터 유사도 0.7 + 키워드 유사도 0.3, 같은 조는 1건만.
"""

import asyncio
import json
import logging
from datetime import date
from pathlib import Path

from app.clients.llm import LLMClient
from app.legal.repository import LawArticle, LawDbUnavailable, law_url

logger = logging.getLogger("chagok.laws")


def _bigrams(text: str) -> set[str]:
    t = "".join(text.split())
    return {t[i : i + 2] for i in range(len(t) - 1)}


class FileLawRepository:
    def __init__(self, path: str | Path, llm: LLMClient):
        self.path = Path(path)
        self.llm = llm
        self._loaded = False
        self._emb = None
        self._rows: list[dict] = []
        self._tags: list[set[str]] = []
        self._by_article: dict[tuple[str, str], dict] = {}
        self._synced: date | None = None

    @property
    def enabled(self) -> bool:
        return self.path.exists()

    def _load(self) -> None:
        if self._loaded:
            return
        import numpy as np

        data = np.load(self.path, allow_pickle=False)
        meta = json.loads(bytes(data["meta"]).decode("utf-8"))
        emb = data["embeddings"].astype(np.float32)
        emb /= np.linalg.norm(emb, axis=1, keepdims=True) + 1e-12
        self._emb = emb
        self._rows = meta["rows"]
        self._tags = [set(r["tags"]) for r in self._rows]
        for r in self._rows:
            key = (r["law"], r["article"])
            if key not in self._by_article or r["paragraph"] is None:
                self._by_article[key] = r
        self._synced = date.fromisoformat(meta["syncedAt"]) if meta.get("syncedAt") else None
        self._loaded = True
        logger.info("law index loaded: %d rows", len(self._rows))

    def _search_sync(self, embedding: list[float], query: str, tags: list[str], k: int) -> list[LawArticle]:
        import numpy as np

        self._load()
        wanted = set(tags)
        mask = np.fromiter((bool(t & wanted) for t in self._tags), dtype=bool, count=len(self._tags))
        if not mask.any():
            return []
        q = np.asarray(embedding, dtype=np.float32)
        q /= np.linalg.norm(q) + 1e-12
        vscore = self._emb @ q
        vscore[~mask] = -1.0

        # 벡터 상위 후보에 키워드(글자 2개씩) 겹침 점수를 더함
        candidates = np.argsort(-vscore)[: k * 10]
        qgrams = _bigrams(query[:300])
        scored = []
        for i in candidates:
            if vscore[i] <= -1.0:
                continue
            row = self._rows[i]
            kscore = len(qgrams & _bigrams(row["content"][:1500])) / len(qgrams) if qgrams else 0.0
            scored.append((float(vscore[i]) * 0.7 + kscore * 0.3, row))
        scored.sort(key=lambda s: s[0], reverse=True)

        results, seen = [], set()
        for score, row in scored:
            key = (row["law"], row["article"])
            if key in seen:
                continue
            seen.add(key)
            results.append(
                LawArticle(law_name=row["law"], article=row["article"], title=row["title"], content=row["content"],
                           url=row["url"] or law_url(row["law"], row["article"]), score=score, paragraph=row["paragraph"])
            )
            if len(results) >= k:
                break
        return results

    async def search(self, query: str, tags: list[str], k: int = 6) -> list[LawArticle]:
        if not self.enabled or not query.strip():
            return []
        try:
            embedding = (await self.llm.embed([query[:2000]]))[0]
            return await asyncio.to_thread(self._search_sync, embedding, query, tags, k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("law file search failed: %s", exc)
            raise LawDbUnavailable() from exc

    async def get_article(self, law_name: str, article: str) -> LawArticle | None:
        if not self.enabled:
            return None
        try:
            await asyncio.to_thread(self._load)
        except Exception as exc:  # noqa: BLE001
            logger.warning("law file load failed: %s", exc)
            return None
        row = self._by_article.get((law_name, article))
        if not row:
            return None
        return LawArticle(law_name=row["law"], article=row["article"], title=row["title"], content=row["content"],
                          url=row["url"] or law_url(row["law"], row["article"]))

    async def latest_sync_date(self) -> date | None:
        if not self.enabled:
            return None
        try:
            await asyncio.to_thread(self._load)
        except Exception:  # noqa: BLE001
            return None
        return self._synced
