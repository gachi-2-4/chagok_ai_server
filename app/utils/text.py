"""문자열·금액·날짜 공통 함수."""

import difflib
import re
from datetime import date

_WS = re.compile(r"\s+")


def normalize(text: str | None) -> str:
    """공백을 모두 지운 비교용 문자열."""
    return _WS.sub("", text or "")


def contains_loose(haystack: str, needle: str | None) -> bool:
    """공백을 무시하고 needle이 haystack 안에 있는지."""
    n = normalize(needle)
    return bool(n) and n in normalize(haystack)


def similarity(a: str | None, b: str | None) -> float:
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def best_window_similarity(haystack: str, needle: str) -> float:
    """haystack 안에서 needle과 가장 비슷한 구간의 유사도 (0~1)."""
    h, n = normalize(haystack), normalize(needle)
    if not h or not n:
        return 0.0
    if n in h:
        return 1.0
    matcher = difflib.SequenceMatcher(None, h, n, autojunk=False)
    match = matcher.find_longest_match(0, len(h), 0, len(n))
    start = max(0, match.a - match.b)
    window = h[start : start + len(n)]
    return difflib.SequenceMatcher(None, window, n).ratio()


# ---------- 날짜 ----------

def parse_iso_date(raw: str | None) -> str | None:
    if not raw:
        return None
    m = re.search(r"(\d{4})[-./년\s]+(\d{1,2})[-./월\s]+(\d{1,2})", str(raw))
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return None


def calc_age(birth_date: str | None, on: date) -> int | None:
    iso = parse_iso_date(birth_date)
    if not iso:
        return None
    b = date.fromisoformat(iso)
    return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


def format_date_dot(iso: str | None) -> str:
    return iso.replace("-", ".") if iso else ""


# ---------- 금액 ----------

_UNITS = {"억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000, "십만": 100_000, "만": 10_000, "천": 1_000, "백": 100}
_AMOUNT_TOKEN = re.compile(r"(\d+(?:\.\d+)?)(억|천만|백만|십만|만|천|백)?")


def parse_amount(raw) -> int | None:
    """'10,000,000', '₩10,000,000원', '2억 3천만원', '55만' → 정수 원."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    s = re.sub(r"[\s,원₩금정]", "", str(raw))
    if not s:
        return None
    negative = s.startswith("-")
    s = s.lstrip("+-")
    if re.fullmatch(r"\d+(\.\d+)?", s):
        value = int(float(s))
        return -value if negative else value
    tokens = _AMOUNT_TOKEN.findall(s)
    if not tokens or "".join(n + u for n, u in tokens) != s:
        digits = re.sub(r"\D", "", s)
        return (-int(digits) if negative else int(digits)) if digits else None
    total = 0
    for number, unit in tokens:
        total += int(float(number) * _UNITS.get(unit, 1))
    return -total if negative else total


def format_won(amount: int | None, signed: bool = False) -> str:
    if amount is None:
        return ""
    if signed:
        sign = "+" if amount > 0 else ("-" if amount < 0 else "")
        return f"{sign}{abs(amount):,}원"
    return f"{amount:,}원"


def format_won_readable(amount: int) -> str:
    """230000000 → '2억 3천만 원' (한 줄 요약용)."""
    if amount < 10_000:
        return f"{amount:,}원"
    eok, rest = divmod(amount, 100_000_000)
    man = rest // 10_000
    parts = []
    if eok:
        parts.append(f"{eok}억")
    if man:
        if man % 1000 == 0:
            parts.append(f"{man // 1000}천만")
        else:
            parts.append(f"{man:,}만")
    return " ".join(parts) + " 원"


# ---------- 마스킹 ----------

def mask_name(name: str | None) -> str:
    if not name:
        return ""
    name = name.strip()
    if len(name) <= 1 or not re.fullmatch(r"[가-힣]{2,5}", name):
        return name
    return name[0] + "○" * (len(name) - 1)


_BUILDING = re.compile(r"[가-힣A-Za-z0-9]+(?=(빌라|아파트|맨션|오피스텔|빌딩|하우스|타운|빌|캐슬))")


def mask_address(address: str | None) -> str:
    if not address:
        return ""
    return _BUILDING.sub("○○", address)


_RRN = re.compile(r"\b(\d{6})-?([1-8])\d{6}\b")
_ACCOUNT = re.compile(r"(계좌(?:번호)?\s*[:：]?\s*(?:[가-힣]+은행\s*)?)([\d-]{8,})")


def mask_sensitive(text: str) -> str:
    text = _RRN.sub(lambda m: f"{m[1]}-{m[2]}******", text)

    def _acc(m: re.Match) -> str:
        digits = m[2]
        keep = 3
        masked = "".join(c if (c == "-" or i < keep) else "*" for i, c in enumerate(digits))
        return m[1] + masked

    return _ACCOUNT.sub(_acc, text)


def cp949_length(text: str) -> int:
    """EUC-KR(CP949) 기준 바이트 수 (문자 종류 SMS/LMS 판단)."""
    return len(text.encode("cp949", errors="replace"))
