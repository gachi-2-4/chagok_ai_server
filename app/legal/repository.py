"""AI 전용 법령 DB (PostgreSQL + pgvector) 조회.

LAW_DB_URL 이 없거나 연결에 실패하면 None/빈 목록을 돌려주고, 호출하는 쪽이 warnings 로 알립니다.
DB 호출은 동기 psycopg를 스레드에서 돌립니다 (Windows 이벤트 루프에서도 동작).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from app.clients.llm import LLMClient

logger = logging.getLogger("chagok.laws")

# 문서 종류 → 법령 분야 태그
DOC_TYPE_TAGS: dict[str, list[str]] = {
    "LEASE_CONTRACT": ["housing_lease", "civil"],
    "EMPLOYMENT_CONTRACT": ["labor", "civil"],
    "SERVICE_CONTRACT": ["consumer", "telecom", "civil", "finance"],
    "BILL": ["consumer", "telecom", "utility"],
    "ADVERTISEMENT": ["consumer", "advertising"],
    "NOTICE": ["civil"],
    "ETC": ["civil"],
}

_SEARCH_SQL = """
    WITH v AS (
        SELECT a.id, 1 - (a.embedding <=> %(emb)s) AS vscore
        FROM law_articles a
        WHERE a.field_tags && %(tags)s AND a.embedding IS NOT NULL
        ORDER BY a.embedding <=> %(emb)s
        LIMIT 40
    ), t AS (
        SELECT a.id, similarity(a.content, %(q)s) AS tscore
        FROM law_articles a
        WHERE a.field_tags && %(tags)s AND a.content %% %(q)s
        ORDER BY tscore DESC
        LIMIT 40
    )
    SELECT l.name, a.article_no, a.article_title, a.paragraph_no, a.content, COALESCE(a.url, l.url),
           COALESCE(v.vscore, 0) * 0.7 + COALESCE(t.tscore, 0) * 0.3 AS score
    FROM law_articles a
    JOIN laws l ON l.id = a.law_id
    LEFT JOIN v ON v.id = a.id
    LEFT JOIN t ON t.id = a.id
    WHERE v.id IS NOT NULL OR t.id IS NOT NULL
    ORDER BY score DESC
    LIMIT %(limit)s
"""


def law_url(law_name: str, article: str | None = None) -> str:
    path = quote(law_name.replace(" ", ""))
    if article:
        path += "/" + quote(article.replace(" ", ""))
    return f"https://www.law.go.kr/법령/{path}"


@dataclass
class LawArticle:
    law_name: str
    article: str
    title: str | None
    content: str
    url: str
    score: float = 0.0
    paragraph: str | None = None


class LawDbUnavailable(Exception):
    pass


class LawRepository:
    def __init__(self, dsn: str, llm: LLMClient):
        self.dsn = dsn
        self.llm = llm

    @property
    def enabled(self) -> bool:
        return bool(self.dsn)

    def _query(self, sql: str, params, one: bool = False):
        import psycopg
        from pgvector.psycopg import register_vector

        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            register_vector(conn)
            cur = conn.execute(sql, params)
            return cur.fetchone() if one else cur.fetchall()

    async def search(self, query: str, tags: list[str], k: int = 6) -> list[LawArticle]:
        """분야 태그로 거른 뒤 벡터 + 키워드(pg_trgm) 혼합 검색. 같은 조는 가장 잘 맞는 1건만."""
        if not self.enabled or not query.strip():
            return []
        try:
            import numpy as np

            embedding = np.array((await self.llm.embed([query[:2000]]))[0])
            rows = await asyncio.to_thread(
                self._query, _SEARCH_SQL, {"emb": embedding, "tags": tags, "q": query[:300], "limit": k * 4}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("law search failed: %s", exc)
            raise LawDbUnavailable() from exc

        results: list[LawArticle] = []
        seen: set[tuple[str, str]] = set()
        for name, article_no, title, paragraph, content, url, score in rows:
            if (name, article_no) in seen:
                continue
            seen.add((name, article_no))
            results.append(
                LawArticle(law_name=name, article=article_no, title=title, content=content,
                           url=url or law_url(name, article_no), score=float(score), paragraph=paragraph)
            )
            if len(results) >= k:
                break
        return results

    async def get_article(self, law_name: str, article: str) -> LawArticle | None:
        if not self.enabled:
            return None
        try:
            row = await asyncio.to_thread(
                self._query,
                """SELECT l.name, a.article_no, a.article_title, a.content, COALESCE(a.url, l.url)
                   FROM law_articles a JOIN laws l ON l.id = a.law_id
                   WHERE l.name = %s AND a.article_no = %s ORDER BY a.paragraph_no NULLS FIRST LIMIT 1""",
                (law_name, article),
                True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("law lookup failed: %s", exc)
            return None
        if not row:
            return None
        return LawArticle(law_name=row[0], article=row[1], title=row[2], content=row[3], url=row[4] or law_url(row[0], row[1]))

    async def latest_sync_date(self) -> date | None:
        if not self.enabled:
            return None
        try:
            row = await asyncio.to_thread(
                self._query, "SELECT max(finished_at)::date FROM sync_logs WHERE status = 'SUCCESS'", None, True
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync log lookup failed: %s", exc)
            return None
        return row[0] if row else None
