import asyncio
import json

import numpy as np

from app.core.config import Settings
from app.core.deps import build_container
from app.legal.file_repository import FileLawRepository
from app.legal.repository import LawRepository


class _Embed:
    def __init__(self, vector):
        self.vector = vector

    async def embed(self, texts):
        return [self.vector for _ in texts]


def _write_index(path):
    rows = [
        {"law": "주택임대차보호법", "article": "제3조의2", "title": "보증금의 회수", "paragraph": None, "content": "임차인은 보증금을 돌려받을 수 있다", "url": "u1", "tags": ["housing_lease"]},
        {"law": "주택임대차보호법", "article": "제3조의2", "title": "보증금의 회수", "paragraph": "①", "content": "① 보증금 반환", "url": "u1", "tags": ["housing_lease"]},
        {"law": "근로기준법", "article": "제69조", "title": "근로시간", "paragraph": None, "content": "연소자 근로시간", "url": "u2", "tags": ["labor"]},
    ]
    emb = np.array([[1, 0, 0], [0.9, 0.1, 0], [0, 1, 0]], dtype=np.float16)
    meta = np.frombuffer(json.dumps({"syncedAt": "2026-11-20", "rows": rows}, ensure_ascii=False).encode(), dtype=np.uint8)
    np.savez_compressed(path, embeddings=emb, meta=meta)


def test_file_index_search(tmp_path):
    path = tmp_path / "law_index.npz"
    _write_index(path)
    repo = FileLawRepository(path, _Embed([1, 0, 0]))

    async def run():
        found = await repo.search("보증금 돌려받", ["housing_lease"], k=5)
        assert [(a.law_name, a.article) for a in found] == [("주택임대차보호법", "제3조의2")]  # 같은 조 1건, 태그 밖 제외
        assert (await repo.search("보증금", ["finance"])) == []
        article = await repo.get_article("주택임대차보호법", "제3조의2")
        assert article.content == "임차인은 보증금을 돌려받을 수 있다"  # 조 전체 우선
        assert (await repo.latest_sync_date()).isoformat() == "2026-11-20"

    asyncio.run(run())


def test_container_picks_backend(tmp_path):
    assert isinstance(build_container(Settings(law_db_url="postgresql://x")).laws, LawRepository)
    file_repo = build_container(Settings(law_db_url="", law_index_path=str(tmp_path / "none.npz"))).laws
    assert isinstance(file_repo, FileLawRepository) and not file_repo.enabled
