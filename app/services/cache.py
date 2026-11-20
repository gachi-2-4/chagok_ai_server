"""작은 메모리 캐시 (요약 카드·질문 칩). 키에 서류 updatedAt 을 넣어 수정되면 자동으로 새로 만듭니다."""

import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: int = 3600, max_items: int = 500):
        self.ttl = ttl_seconds
        self.max_items = max_items
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def get(self, key):
        item = self._data.get(key)
        if not item:
            return None
        expires, value = item
        if expires < time.time():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key, value) -> None:
        self._data[key] = (time.time() + self.ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_items:
            self._data.popitem(last=False)


class RateLimiter:
    """사용자당 분당 요청 수 제한."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[Any, list[float]] = {}

    def allow(self, key) -> bool:
        now = time.time()
        hits = [t for t in self._hits.get(key, []) if now - t < 60]
        if len(hits) >= self.per_minute:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True
