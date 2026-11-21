"""법령 DB → 법령 검색 파일(data/law_index.npz). 작은 서버에서는 DB 대신 이 파일로 검색합니다.

    python -m scripts.export_law_index                 # LAW_DB_URL 의 DB에서 만들기
    python -m scripts.export_law_index --out other.npz

법령을 다시 동기화한 뒤 이 스크립트를 다시 실행하고, 만든 파일을 서버에 올리면 됩니다.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "data" / "law_index.npz"))
    args = parser.parse_args()

    import numpy as np
    import psycopg
    from pgvector.psycopg import register_vector

    dsn = get_settings().law_db_url
    if not dsn:
        print("LAW_DB_URL 이 비어 있어요.")
        return 1
    with psycopg.connect(dsn) as conn:
        register_vector(conn)
        rows = conn.execute(
            """SELECT l.name, a.article_no, a.article_title, a.paragraph_no, a.content, a.url, a.field_tags, a.embedding
               FROM law_articles a JOIN laws l ON l.id = a.law_id
               WHERE a.embedding IS NOT NULL
               ORDER BY l.name, a.id"""
        ).fetchall()
        synced = conn.execute("SELECT max(finished_at)::date FROM sync_logs WHERE status = 'SUCCESS'").fetchone()[0]

    if not rows:
        print("법령 DB가 비어 있어요. 먼저 python -m scripts.sync_laws 를 실행하세요.")
        return 1

    meta = {
        "syncedAt": synced.isoformat() if synced else None,
        "rows": [
            {"law": r[0], "article": r[1], "title": r[2], "paragraph": r[3], "content": r[4], "url": r[5], "tags": list(r[6])}
            for r in rows
        ],
    }
    embeddings = np.stack([np.asarray(r[7].to_numpy() if hasattr(r[7], "to_numpy") else r[7], dtype=np.float16) for r in rows])
    meta_bytes = np.frombuffer(json.dumps(meta, ensure_ascii=False).encode("utf-8"), dtype=np.uint8)
    np.savez_compressed(args.out, embeddings=embeddings, meta=meta_bytes)
    size = Path(args.out).stat().st_size / 1024 / 1024
    print(f"saved {len(rows)} rows → {args.out} ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
