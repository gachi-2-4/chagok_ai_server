"""국가법령정보센터 Open API → 법령 DB 동기화 (주 1회 실행).

    python -m scripts.sync_laws            # data/target_laws.json 의 법령만 동기화
    python -m scripts.sync_laws --init     # 스키마 생성 후 동기화
    python -m scripts.sync_laws --dry-run  # DB에 쓰지 않고 받아오기만

필요한 환경변수: LAW_API_OC (open.law.go.kr 신청 때 직접 정한 API 인증키), LAW_DB_URL, OPENAI_API_KEY(임베딩)
등록한 서버 IP에서만 호출됩니다. 응답 형식은 승인 후 '정보공개 가이드 조회'에서 한 번 확인하세요.
"""

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sync_laws")

API = "https://www.law.go.kr/DRF"
EMBED_BATCH = 100


@dataclass
class Article:
    article_no: str
    title: str | None
    paragraph_no: str | None
    content: str
    effective_date: date | None


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(value) -> str:
    if isinstance(value, list):
        return "\n".join(_text(v) for v in value)
    return str(value or "").strip()


def _date(raw) -> date | None:
    raw = str(raw or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
    return None


def search_law(client: httpx.Client, oc: str, name: str) -> dict | None:
    """법령명으로 현행 법령 1건 찾기 (현행법령(시행일) 목록 조회, nw=3: 현행만)."""
    res = client.get(f"{API}/lawSearch.do", params={"OC": oc, "target": "eflaw", "type": "JSON", "query": name, "nw": 3, "display": 20})
    res.raise_for_status()
    items = _as_list(res.json().get("LawSearch", {}).get("law"))
    exact = [i for i in items if i.get("법령명한글", "").replace(" ", "") == name.replace(" ", "")]
    current = [i for i in exact if i.get("현행연혁코드", "현행") == "현행"] or exact
    return current[0] if current else None


def fetch_law(client: httpx.Client, oc: str, law_id: str) -> dict:
    """현행법령(시행일) 본문 조회. ID로 부르면 그 법령의 현행 본문이 옵니다 (efYd 불필요)."""
    res = client.get(f"{API}/lawService.do", params={"OC": oc, "target": "eflaw", "type": "JSON", "ID": law_id})
    res.raise_for_status()
    return res.json().get("법령", {})


def parse_articles(law: dict) -> list[Article]:
    """조문단위 → 조 전체 1건 + 항마다 1건 (항이 있으면)."""
    out: list[Article] = []
    for unit in _as_list(law.get("조문", {}).get("조문단위")):
        if unit.get("조문여부") and unit.get("조문여부") != "조문":
            continue  # 장·절 제목 제외
        number = str(unit.get("조문번호", "")).strip()
        branch = str(unit.get("조문가지번호", "") or "").strip()
        if not number:
            continue
        article_no = f"제{number}조" + (f"의{branch}" if branch else "")
        title = unit.get("조문제목")
        effective = _date(unit.get("조문시행일자"))
        head = _text(unit.get("조문내용"))
        paragraphs = _as_list(unit.get("항"))

        para_texts = []
        for p in paragraphs:
            if not isinstance(p, dict):
                continue
            body = _text(p.get("항내용"))
            for h in _as_list(p.get("호")):
                if isinstance(h, dict):
                    body += "\n" + _text(h.get("호내용"))
                    for m in _as_list(h.get("목")):
                        if isinstance(m, dict):
                            body += "\n" + _text(m.get("목내용"))
            if body.strip():
                para_texts.append((str(p.get("항번호", "")).strip() or None, body.strip()))

        whole = "\n".join([head] + [t for _, t in para_texts]).strip()
        if whole:
            out.append(Article(article_no, title, None, whole, effective))
        if len(para_texts) > 1:
            for number_p, text in para_texts:
                out.append(Article(article_no, title, number_p, f"{head}\n{text}".strip(), effective))
    return out


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def article_url(law_name: str, article_no: str) -> str:
    from urllib.parse import quote

    return f"https://www.law.go.kr/법령/{quote(law_name.replace(' ', ''))}/{quote(article_no)}"


def embed(texts: list[str]) -> list[list[float]]:
    from openai import OpenAI

    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        res = client.embeddings.create(model=settings.openai_embedding_model, input=[t[:6000] for t in texts[i : i + EMBED_BATCH]])
        vectors += [d.embedding for d in res.data]
    return vectors


def upsert(conn, target: dict, meta: dict, law: dict, articles: list[Article]) -> int:
    import numpy as np

    info = law.get("기본정보", {})
    name = target["name"]
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO laws (law_id, law_mst, name, law_type, ministry, promulgation_date, effective_date, url, field_tags, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
               ON CONFLICT (law_id) DO UPDATE SET law_mst = EXCLUDED.law_mst, name = EXCLUDED.name, law_type = EXCLUDED.law_type,
                 ministry = EXCLUDED.ministry, promulgation_date = EXCLUDED.promulgation_date, effective_date = EXCLUDED.effective_date,
                 url = EXCLUDED.url, field_tags = EXCLUDED.field_tags, updated_at = now()
               RETURNING id""",
            (
                str(meta.get("법령ID") or info.get("법령ID")),
                str(meta.get("법령일련번호")),
                name,
                _text(info.get("법종구분", {}).get("content") if isinstance(info.get("법종구분"), dict) else info.get("법종구분")) or None,
                meta.get("소관부처명"),
                _date(meta.get("공포일자")),
                _date(meta.get("시행일자")),
                article_url(name, "").rstrip("/"),
                target["tags"],
            ),
        )
        law_pk = cur.fetchone()[0]

        cur.execute("SELECT article_no, paragraph_no, content_hash FROM law_articles WHERE law_id = %s", (law_pk,))
        existing = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        changed = [a for a in articles if existing.get((a.article_no, a.paragraph_no)) != _hash(a.content)]
        keep = {(a.article_no, a.paragraph_no) for a in articles}
        removed = [k for k in existing if k not in keep]
        for article_no, paragraph_no in removed:
            cur.execute(
                "DELETE FROM law_articles WHERE law_id = %s AND article_no = %s AND paragraph_no IS NOT DISTINCT FROM %s",
                (law_pk, article_no, paragraph_no),
            )

        vectors = embed([f"{name} {a.article_no} {a.title or ''}\n{a.content}" for a in changed]) if changed else []
        for a, vector in zip(changed, vectors):
            cur.execute(
                """INSERT INTO law_articles (law_id, article_no, article_title, paragraph_no, content, content_hash, effective_date, url, field_tags, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (law_id, article_no, paragraph_no) DO UPDATE SET article_title = EXCLUDED.article_title,
                     content = EXCLUDED.content, content_hash = EXCLUDED.content_hash, effective_date = EXCLUDED.effective_date,
                     url = EXCLUDED.url, field_tags = EXCLUDED.field_tags, embedding = EXCLUDED.embedding""",
                (law_pk, a.article_no, a.title, a.paragraph_no, a.content, _hash(a.content), a.effective_date,
                 article_url(name, a.article_no), target["tags"], np.array(vector)),
            )
        cur.execute("UPDATE law_articles SET field_tags = %s WHERE law_id = %s", (target["tags"], law_pk))
    return len(changed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true", help="db/schema.sql 실행")
    parser.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않음")
    parser.add_argument("--only", help="이 이름의 법령만")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.law_api_oc:
        log.error("LAW_API_OC 가 비어 있어요. open.law.go.kr 에서 신청한 API 인증키(OC)를 넣어 주세요.")
        return 1
    targets = json.loads((ROOT / "data" / "target_laws.json").read_text(encoding="utf-8"))["laws"]
    if args.only:
        targets = [t for t in targets if t["name"] == args.only]

    conn = None
    log_id = None
    if not args.dry_run:
        import psycopg
        from pgvector.psycopg import register_vector

        if not settings.law_db_url:
            log.error("LAW_DB_URL 이 비어 있어요.")
            return 1
        conn = psycopg.connect(settings.law_db_url, autocommit=True)
        if args.init:
            conn.execute((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
        register_vector(conn)
        log_id = conn.execute("INSERT INTO sync_logs DEFAULT VALUES RETURNING id").fetchone()[0]

    laws_updated = articles_updated = 0
    failures = []
    with httpx.Client(timeout=30) as client:
        for target in targets:
            try:
                meta = search_law(client, settings.law_api_oc, target["name"])
                if not meta:
                    failures.append(f"{target['name']}: 검색 결과 없음")
                    log.warning("not found: %s", target["name"])
                    continue
                law = fetch_law(client, settings.law_api_oc, str(meta["법령ID"]))
                articles = parse_articles(law)
                log.info("%s: %d articles (MST %s)", target["name"], len(articles), meta["법령일련번호"])
                if conn is not None:
                    with conn.transaction():
                        articles_updated += upsert(conn, target, meta, law, articles)
                    laws_updated += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{target['name']}: {exc}")
                log.exception("failed: %s", target["name"])

    if conn is not None:
        conn.execute(
            "UPDATE sync_logs SET finished_at = now(), status = %s, laws_updated = %s, articles_updated = %s, message = %s WHERE id = %s",
            ("FAILED" if failures and not laws_updated else "SUCCESS", laws_updated, articles_updated, "\n".join(failures) or None, log_id),
        )
        conn.close()
    log.info("done: laws=%d articles(changed)=%d failures=%d", laws_updated, articles_updated, len(failures))
    return 1 if failures and not laws_updated else 0


if __name__ == "__main__":
    raise SystemExit(main())
