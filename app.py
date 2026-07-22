from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
import unicodedata
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from supabase import Client, create_client


# ============================================================
# 프롭테크 하이퍼 엔진 V29.0 Supabase Edition
# - 단일 Python 파일
# - 하나의 통합 원문 입력창
# - 2초 디바운스 후 자동 분류
# - 사용자 확인 후 Supabase 저장
# - 저장 성공 시 HUD 표시 및 입력창 초기화
# ============================================================

st.set_page_config(
    page_title="프롭테크 하이퍼 엔진 V29.0",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
    h1, h2, h3 { color: #0F172A; font-weight: 800; }
    .stAlert { border-radius: 10px; }
    code { font-family: 'Pretendard', sans-serif !important; font-size: 0.95rem !important; }
    div[data-testid="stMetricValue"] { font-size: 1.8rem; font-weight: 700; color: #1E293B; }
    .hud-card {
        border: 1px solid #86efac;
        background: linear-gradient(135deg, #f0fdf4, #ecfeff);
        border-radius: 14px;
        padding: 14px 16px;
        margin: 6px 0 12px 0;
        box-shadow: 0 5px 16px rgba(15,23,42,.08);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

SUPABASE_TABLE = "real_estate_records"
DEFAULT_SUPABASE_URL = "https://lyxfwtwqwlujxszezkud.supabase.co"

CATEGORY_TX = "실거래"
CATEGORY_SALE = "매매호가"
CATEGORY_RENTAL = "전월세"
CATEGORY_RAW = "원문"
SCREENING_OPTIONS = ["실거래가", "매매호가", "전월세"]

SCHEMA_SQL = """
create table if not exists public.real_estate_records (
    record_hash text primary key,
    complex_name text not null,
    category text not null check (
        category in ('실거래', '매매호가', '전월세', '원문')
    ),
    snapshot_date date not null,
    payload jsonb not null,
    created_at timestamptz not null default now()
);

create index if not exists real_estate_records_complex_idx
    on public.real_estate_records (complex_name);

create index if not exists real_estate_records_category_idx
    on public.real_estate_records (category);

create index if not exists real_estate_records_snapshot_idx
    on public.real_estate_records (snapshot_date desc);

alter table public.real_estate_records enable row level security;

-- 이 앱은 Streamlit 서버의 Supabase Secret key로 접근합니다.
-- anon/authenticated 역할에는 공개 정책을 만들지 않습니다.
"""


# ============================================================
# Secrets / Supabase
# ============================================================

def _read_secret_section(name: str) -> dict:
    try:
        section = st.secrets.get(name, {})
        return dict(section) if section else {}
    except (FileNotFoundError, KeyError, TypeError):
        return {}


def get_supabase_config() -> tuple[str, str]:
    section = _read_secret_section("supabase")
    url = str(section.get("url") or DEFAULT_SUPABASE_URL).strip()
    key = str(section.get("key") or section.get("secret_key") or "").strip()
    return url, key


def get_app_config() -> dict:
    return _read_secret_section("app")


@st.cache_resource(show_spinner=False)
def get_supabase_client(url: str, key: str) -> Client:
    if not url or not key:
        raise RuntimeError(
            "Supabase URL 또는 Secret key가 없습니다. "
            "Streamlit Secrets에 [supabase] url과 key를 등록하세요."
        )
    return create_client(url, key)


def supabase_client() -> Client:
    url, key = get_supabase_config()
    return get_supabase_client(url, key)


def test_supabase_connection() -> tuple[bool, str]:
    try:
        response = (
            supabase_client()
            .table(SUPABASE_TABLE)
            .select("record_hash")
            .limit(1)
            .execute()
        )
        _ = response.data
        return True, "Supabase 연결 정상"
    except Exception as exc:
        return False, str(exc)


def is_admin_authenticated() -> bool:
    app_config = get_app_config()
    configured_password = str(app_config.get("admin_password") or "")
    allow_public_write = bool(app_config.get("allow_public_write", False))

    if allow_public_write:
        return True
    if not configured_password:
        return False
    return bool(st.session_state.get("admin_authenticated", False))


def render_admin_login() -> None:
    app_config = get_app_config()
    configured_password = str(app_config.get("admin_password") or "")
    allow_public_write = bool(app_config.get("allow_public_write", False))

    if allow_public_write:
        st.success("관리 기능: 공개 쓰기 허용")
        return

    if not configured_password:
        st.warning("Secrets에 app.admin_password를 설정해야 입력·삭제 기능이 활성화됩니다.")
        return

    if st.session_state.get("admin_authenticated", False):
        st.success("🔓 관리자 모드")
        if st.button("관리자 로그아웃", use_container_width=True):
            st.session_state["admin_authenticated"] = False
            st.rerun()
        return

    password = st.text_input(
        "관리자 비밀번호",
        type="password",
        key="admin_password_input",
    )
    if st.button("관리자 로그인", use_container_width=True):
        if hmac.compare_digest(password, configured_password):
            st.session_state["admin_authenticated"] = True
            st.success("관리자 인증 완료")
            st.rerun()
        else:
            st.error("비밀번호가 맞지 않습니다.")


# ============================================================
# Supabase JSON 저장 계층
# ============================================================

def json_safe(value: Any) -> Any:
    if value is None:
        return None
    if value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    return value


def normalize_snapshot_date(value: Any) -> str:
    if value is None or value is pd.NA:
        return datetime.now().strftime("%Y-%m-%d")

    text = str(value).strip().replace(".", "-").replace("/", "-")
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return datetime.now().strftime("%Y-%m-%d")
    return parsed.strftime("%Y-%m-%d")


def infer_snapshot_date(category: str, payload: dict) -> str:
    if category == CATEGORY_TX:
        return normalize_snapshot_date(payload.get("날짜"))
    if category in (CATEGORY_SALE, CATEGORY_RENTAL):
        return normalize_snapshot_date(payload.get("수집일"))
    return normalize_snapshot_date(payload.get("날짜"))


def make_record_hash(
    complex_name: str,
    category: str,
    snapshot_date: str,
    payload: dict,
) -> str:
    canonical = json.dumps(
        {
            "complex_name": complex_name,
            "category": category,
            "snapshot_date": snapshot_date,
            "payload": json_safe(payload),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def dataframe_to_supabase_rows(
    df: pd.DataFrame,
    complex_name: str,
    category: str,
) -> list[dict]:
    if df.empty:
        return []

    rows: list[dict] = []
    for record in df.to_dict(orient="records"):
        payload = {str(k): json_safe(v) for k, v in record.items()}
        payload["단지명"] = complex_name
        snapshot_date = infer_snapshot_date(category, payload)
        rows.append(
            {
                "record_hash": make_record_hash(
                    complex_name,
                    category,
                    snapshot_date,
                    payload,
                ),
                "complex_name": complex_name,
                "category": category,
                "snapshot_date": snapshot_date,
                "payload": payload,
            }
        )
    return rows


def upsert_dataframe(
    df: pd.DataFrame,
    complex_name: str,
    category: str,
) -> int:
    rows = dataframe_to_supabase_rows(df, complex_name, category)
    if not rows:
        return 0

    client = supabase_client()
    chunk_size = 300
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start:start + chunk_size]
        (
            client.table(SUPABASE_TABLE)
            .upsert(
                chunk,
                on_conflict="record_hash",
                ignore_duplicates=False,
            )
            .execute()
        )
    return len(rows)


def save_raw_input(
    raw_text: str,
    complex_name: str,
    raw_type: str,
) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    raw_df = pd.DataFrame(
        [{
            "날짜": today,
            "단지명": complex_name,
            "유형": raw_type,
            "원문": raw_text,
        }]
    )
    return upsert_dataframe(raw_df, complex_name, CATEGORY_RAW)


def fetch_supabase_rows(
    *,
    complex_name: Optional[str] = None,
    category: Optional[str] = None,
    page_size: int = 1000,
) -> list[dict]:
    client = supabase_client()
    result: list[dict] = []
    start = 0

    while True:
        query = (
            client.table(SUPABASE_TABLE)
            .select(
                "record_hash,complex_name,category,"
                "snapshot_date,payload,created_at"
            )
            .order("created_at", desc=False)
        )
        if complex_name:
            query = query.eq("complex_name", complex_name)
        if category:
            query = query.eq("category", category)

        response = query.range(start, start + page_size - 1).execute()
        page = response.data or []
        result.extend(page)

        if len(page) < page_size:
            break
        start += page_size

    return result


def records_to_dataframe(records: list[dict]) -> pd.DataFrame:
    payloads = []
    for record in records:
        payload = dict(record.get("payload") or {})
        payload.setdefault("단지명", record.get("complex_name"))
        payloads.append(payload)
    return pd.DataFrame(payloads)


def load_category_df(
    complex_name: str,
    category: str,
) -> pd.DataFrame:
    try:
        return records_to_dataframe(
            fetch_supabase_rows(
                complex_name=complex_name,
                category=category,
            )
        )
    except Exception:
        return pd.DataFrame()


def load_all_complex_names() -> list[str]:
    try:
        records = fetch_supabase_rows()
    except Exception:
        return []

    names = {
        str(record.get("complex_name")).strip()
        for record in records
        if record.get("category") != CATEGORY_RAW
        and record.get("complex_name")
    }
    return sorted(names)


def delete_complex_data(complex_name: str) -> bool:
    try:
        (
            supabase_client()
            .table(SUPABASE_TABLE)
            .delete()
            .eq("complex_name", complex_name)
            .execute()
        )
        return True
    except Exception:
        return False


def rollback_latest_snapshot() -> tuple[bool, str]:
    try:
        response = (
            supabase_client()
            .table(SUPABASE_TABLE)
            .select("snapshot_date")
            .order("snapshot_date", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return False, "롤백할 데이터가 없습니다."

        latest_date = rows[0]["snapshot_date"]
        (
            supabase_client()
            .table(SUPABASE_TABLE)
            .delete()
            .eq("snapshot_date", latest_date)
            .execute()
        )
        return True, f"{latest_date} 스냅샷을 삭제했습니다."
    except Exception as exc:
        return False, str(exc)


def dataframe_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


# ============================================================
# 공통 분석 함수
# ============================================================

def categorize_floor(floor_str: Any) -> str:
    f_str = str(floor_str).strip()
    if "탑" in f_str:
        return "탑층"
    if "고" in f_str:
        return "고층"
    if "중" in f_str:
        return "중층"
    if "저" in f_str:
        return "저층"

    nums = re.findall(r"\d+", f_str)
    if nums:
        floor_num = int(nums[0])
        total_floor = int(nums[1]) if len(nums) > 1 else 35
        if floor_num == total_floor and total_floor > 10:
            return "탑층"
        ratio = floor_num / total_floor
        if ratio <= 0.3:
            return "저층"
        if ratio <= 0.7:
            return "중층"
        return "고층"
    return "중층"


# ============================================================
# 통합 파서
# ============================================================

EOK_AMOUNT_PATTERN = (
    r"(?:\d+\s*억"
    r"(?:\s*\d+\s*천\s*\d{0,3}"
    r"|\s*\d{1,3}(?:,\d{3})+"
    r"|\s*\d{1,4})?)"
)
GENERAL_AMOUNT_PATTERN = (
    rf"(?:{EOK_AMOUNT_PATTERN}|\d{{1,3}}(?:,\d{{3}})+|\d+)"
)

LISTING_HEADER_RE = re.compile(
    r"(?m)^(?P<complex>[^\n]{1,100}?)\s+"
    r"(?P<dong>\d{1,4}동)\s*\n\s*"
    r"(?P<deal>매매|전세|월세)\s+"
    r"(?P<price>[^\n]+)"
)


def normalize_raw_text(text: str) -> str:
    """Keep line structure but normalize copied web text."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def canonical_complex_name(name: str) -> str:
    """Compare names such as 범어자이(주상복합), 범어자이분양권."""
    name = normalize_raw_text(name)
    name = re.sub(r"\([^)]*\)", "", name)
    name = re.sub(r"(주상복합|분양권|아파트)", "", name)
    name = re.sub(r"[^0-9A-Za-z가-힣]", "", name)
    return name.lower()


def is_same_complex(found: str, expected: Optional[str]) -> bool:
    if not expected:
        return True
    a = canonical_complex_name(found)
    b = canonical_complex_name(expected)
    return bool(a and b and (a == b or a in b or b in a))


def is_valid_date(year, month, day) -> bool:
    try:
        datetime(int(year), int(month), int(day))
        return True
    except (TypeError, ValueError):
        return False


def convert_price_single(value) -> float:
    """
    Korean price -> 억.
    Examples:
      11억 2,044 -> 11.2044
      15억7천238 -> 15.7238
      10억44     -> 10.0044
      5,000      -> 0.5
    """
    if value is None or pd.isna(value):
        return 0.0

    s = unicodedata.normalize("NFKC", str(value))
    s = re.sub(
        r"(최고|최저|신고가|직거래|중개거래|변동상승내역\s*보기|\(고\))",
        "",
        s,
    )
    s = s.replace(",", "").replace(" ", "").replace("\n", "").strip()

    if not s:
        return 0.0

    if "억" in s:
        eok_text, rest = s.split("억", 1)
        eok_match = re.search(r"\d+", eok_text)
        eok = int(eok_match.group()) if eok_match else 0

        man = 0
        if "천" in rest:
            cheon_text, tail_text = rest.split("천", 1)
            cheon_match = re.search(r"\d+", cheon_text)
            tail_match = re.search(r"\d+", tail_text)
            man += (int(cheon_match.group()) if cheon_match else 0) * 1000
            man += int(tail_match.group()) if tail_match else 0
        else:
            rest_match = re.search(r"\d+", rest)
            man = int(rest_match.group()) if rest_match else 0

        return eok + man / 10000.0

    if "천" in s:
        cheon_text, tail_text = s.split("천", 1)
        cheon_match = re.search(r"\d+", cheon_text)
        tail_match = re.search(r"\d+", tail_text)
        man = (int(cheon_match.group()) if cheon_match else 0) * 1000
        man += int(tail_match.group()) if tail_match else 0
        return man / 10000.0

    digits = re.sub(r"\D", "", s)
    return int(digits) / 10000.0 if digits else 0.0


def extract_price_range(raw: str) -> Tuple[float, float, float]:
    raw = normalize_raw_text(raw).replace("\n", " ")
    parts = re.split(r"\s*[~∼]\s*", raw, maxsplit=1)
    low = convert_price_single(parts[0])
    high = convert_price_single(parts[1]) if len(parts) > 1 else low
    if high < low:
        low, high = high, low
    return low, high, (low + high) / 2


def process_price_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "금액_문자열" not in df.columns:
        return df

    df = df.copy()
    values = df["금액_문자열"].apply(extract_price_range)
    df[["금액_하한(억)", "금액_상한(억)", "금액_평균(억)"]] = pd.DataFrame(
        values.tolist(), index=df.index
    )
    # Backward compatibility with the existing dashboard.
    df["금액(억)"] = df["금액_하한(억)"]
    return df


def extract_listing_price(deal: str, raw_line: str) -> Optional[str]:
    raw_line = normalize_raw_text(raw_line).replace("\n", " ")

    if deal == "월세":
        pattern = re.compile(
            rf"(?P<p1>{GENERAL_AMOUNT_PATTERN})\s*/\s*(?P<r1>[\d,]+)"
            rf"(?:\s*[~∼]\s*"
            rf"(?P<p2>{GENERAL_AMOUNT_PATTERN})\s*/\s*(?P<r2>[\d,]+))?"
        )
    else:
        pattern = re.compile(
            rf"(?P<p1>{GENERAL_AMOUNT_PATTERN})"
            rf"(?:\s*[~∼]\s*(?P<p2>{GENERAL_AMOUNT_PATTERN}))?"
        )

    match = pattern.search(raw_line)
    return match.group(0).strip() if match else None


def parse_monthly_price(raw: str) -> Dict[str, float]:
    pattern = re.compile(
        rf"({GENERAL_AMOUNT_PATTERN})\s*/\s*([\d,]+)"
    )
    pairs = pattern.findall(raw or "")

    if not pairs:
        return {
            "보증금(억)": 0.0,
            "보증금_하한(억)": 0.0,
            "보증금_상한(억)": 0.0,
            "월세(만원)": 0,
            "월세_하한(만원)": 0,
            "월세_상한(만원)": 0,
        }

    deposits = [convert_price_single(dep) for dep, _ in pairs]
    rents = [int(rent.replace(",", "")) for _, rent in pairs]

    return {
        # First condition is retained as the representative condition.
        "보증금(억)": deposits[0],
        "보증금_하한(억)": min(deposits),
        "보증금_상한(억)": max(deposits),
        "월세(만원)": rents[0],
        "월세_하한(만원)": min(rents),
        "월세_상한(만원)": max(rents),
    }


def normalize_type(area: Optional[str], suffix: str = "") -> str:
    if not area:
        return "미상"
    try:
        number = f"{float(area):.2f}".rstrip("0").rstrip(".")
    except ValueError:
        number = str(area).strip()
    return f"{number}{suffix or ''}"


def find_broker_name(block: str) -> str:
    for line in normalize_raw_text(block).splitlines():
        line = line.strip()
        if re.search(r"중개사\s*\d+곳", line):
            continue
        if "공인중개사" in line or "부동산중개" in line:
            return line
    return ""


def parse_naver_property_cards(
    text: str,
    expected_complex: Optional[str] = None,
) -> Tuple[pd.DataFrame, Dict]:
    """
    Parse sale, jeonse, and monthly-rent cards in one pass.
    The page may contain more than one section and may be pasted together.
    """
    text = normalize_raw_text(text)
    matches = list(LISTING_HEADER_RE.finditer(text))
    records: List[Dict] = []
    foreign_complexes = set()
    rejected = 0
    today_str = datetime.now().strftime("%Y-%m-%d")

    for index, match in enumerate(matches):
        block_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.start():block_end]

        found_complex = match.group("complex").strip()
        if not is_same_complex(found_complex, expected_complex):
            foreign_complexes.add(found_complex)
            continue

        deal = match.group("deal")
        price_raw = extract_listing_price(deal, match.group("price"))
        if not price_raw:
            rejected += 1
            continue

        area_match = re.search(
            r"전용\s*(?P<area>\d+(?:\.\d+)?)(?P<suffix>[A-Za-z]?)",
            block,
        )
        floor_match = re.search(
            r"\)\s*(?P<floor>저|중|고|탑|\d{1,2})/"
            r"(?P<total>\d{1,2})층\s*"
            r"(?P<direction>남서|남동|북서|북동|남|북|동|서)향",
            block,
        )
        if not floor_match:
            floor_match = re.search(
                r"(?P<floor>저|중|고|탑|\d{1,2})/"
                r"(?P<total>\d{1,2})층\s*"
                r"(?P<direction>남서|남동|북서|북동|남|북|동|서)향",
                block,
            )

        date_match = re.search(
            r"(?:집주인확인매물|확인매물|등록)\s*"
            r"(?P<date>20\d{2}\.\d{2}\.\d{2})",
            block,
        )
        broker_count_match = re.search(r"중개사\s*(\d+)곳", block)

        area = area_match.group("area") if area_match else None
        suffix = area_match.group("suffix").upper() if area_match else ""
        floor = floor_match.group("floor") if floor_match else "미상"
        total_floor = floor_match.group("total") if floor_match else ""
        floor_value = f"{floor}/{total_floor}" if total_floor else floor
        direction = (
            f"{floor_match.group('direction')}향"
            if floor_match
            else "방향미상"
        )

        base = {
            "수집일": today_str,
            "매물등록일": date_match.group("date") if date_match else today_str.replace("-", "."),
            "원문단지명": found_complex,
            "동": match.group("dong"),
            "타입": normalize_type(area, suffix),
            "전용면적(㎡)": float(area) if area else pd.NA,
            "층": floor_value,
            "총층": int(total_floor) if total_floor else pd.NA,
            "방향": direction,
            "중개사수": int(broker_count_match.group(1)) if broker_count_match else 1,
            "중개사명": find_broker_name(block),
            "금액_문자열": price_raw,
            "파서형식": "네이버매물카드",
            "파싱신뢰도": 1.0 if area_match and floor_match and date_match else 0.8,
        }

        if deal == "매매":
            base.update({
                "거래구분": "매매",
                "데이터구분": "네이버매물(매매)",
            })
            records.append(base)

        elif deal == "전세":
            low, high, avg = extract_price_range(price_raw)
            base.update({
                "거래구분": "전세",
                "보증금(억)": low,
                "보증금_하한(억)": low,
                "보증금_상한(억)": high,
                "보증금_평균(억)": avg,
                "월세(만원)": 0,
                "데이터구분": "네이버매물(전월세)",
            })
            records.append(base)

        else:
            base.update(parse_monthly_price(price_raw))
            base.update({
                "거래구분": "월세",
                "데이터구분": "네이버매물(전월세)",
            })
            records.append(base)

    df = pd.DataFrame(records)
    report = {
        "listing_card_candidates": len(matches),
        "listing_card_rejected": rejected,
        "foreign_complexes": sorted(foreign_complexes),
    }
    return df, report


def parse_tx_naver_table(text: str) -> pd.DataFrame:
    """Format 1: '6월 23일 ... 27층 ... 10억 7,014'."""
    text = normalize_raw_text(text)
    if "실거래가 표" not in text and "계약일" not in text:
        return pd.DataFrame()

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    current_year = datetime.now().year
    records = []
    index = 0

    while index < len(lines):
        line = lines[index]

        year_match = re.search(r"(20\d{2})년", line)
        if year_match:
            current_year = int(year_match.group(1))

        date_match = re.match(r"^(\d{1,2})월\s*(\d{1,2})일\b", line)
        if not date_match:
            index += 1
            continue

        block_lines = [line]
        cursor = index + 1
        while cursor < len(lines):
            next_line = lines[cursor]
            if re.search(r"(20\d{2})년", next_line):
                break
            if re.match(r"^\d{1,2}월\s*\d{1,2}일\b", next_line):
                break
            block_lines.append(next_line)
            cursor += 1

        block = " ".join(block_lines)
        index = cursor

        if "계약취소" in block or "해지" in block:
            continue

        month, day = map(int, date_match.groups())
        if not is_valid_date(current_year, month, day):
            continue

        floor_match = re.search(r"(\d{1,2})층", block)
        price_match = re.search(EOK_AMOUNT_PATTERN, block)
        if not floor_match or not price_match:
            continue

        records.append({
            "날짜": f"{current_year:04d}.{month:02d}.{day:02d}",
            "타입": "전체",
            "전용면적(㎡)": pd.NA,
            "금액_문자열": price_match.group(0).strip(),
            "층": floor_match.group(1),
            "동": "동미상",
            "거래유형": "직거래" if "직거래" in block else "중개거래",
            "권리유형": "분양권" if "분양권" in block else "매매",
            "데이터구분": "실거래",
            "파서형식": "실거래_네이버표",
            "파싱신뢰도": 0.92,
        })

    return pd.DataFrame(records)


def parse_tx_vertical_list(text: str) -> pd.DataFrame:
    """
    Format 3:
      07.17
      114.9694㎡ 47평 · 21층
      분양권
      15억7천238
    """
    text = normalize_raw_text(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    records = []
    current_year = datetime.now().year
    index = 0

    while index < len(lines):
        line = lines[index]

        year_match = re.search(r"(20\d{2})년", line)
        if year_match:
            current_year = int(year_match.group(1))

        date_match = re.fullmatch(r"(\d{2})\.(\d{2})", line)
        if not date_match:
            index += 1
            continue

        block_lines = [line]
        cursor = index + 1
        while cursor < len(lines):
            next_line = lines[cursor]
            if re.search(r"(20\d{2})년", next_line):
                break
            if re.fullmatch(r"\d{2}\.\d{2}", next_line):
                break
            block_lines.append(next_line)
            cursor += 1

        block = "\n".join(block_lines)
        index = cursor

        if "해지" in block or "계약취소" in block:
            continue

        area_match = re.search(
            r"(?P<area>\d+(?:\.\d+)?)(?:㎡|m2|m²)\s*"
            r"(?P<pyung>\d+)(?P<suffix>[A-Za-z]?)평\s*·\s*"
            r"(?P<floor>\d{1,2})층",
            block,
        )
        price_match = re.search(EOK_AMOUNT_PATTERN, block)

        if not area_match or not price_match:
            continue

        month, day = map(int, date_match.groups())
        if not is_valid_date(current_year, month, day):
            continue

        suffix = area_match.group("suffix").upper()
        records.append({
            "날짜": f"{current_year:04d}.{month:02d}.{day:02d}",
            "타입": normalize_type(area_match.group("area"), suffix),
            "전용면적(㎡)": float(area_match.group("area")),
            "평형": f"{area_match.group('pyung')}{suffix}",
            "금액_문자열": price_match.group(0).strip(),
            "층": area_match.group("floor"),
            "동": "동미상",
            "거래유형": "직거래" if "직거래" in block else "중개거래",
            "권리유형": "분양권" if "분양권" in block else "매매",
            "데이터구분": "실거래",
            "파서형식": "실거래_세로목록",
            "파싱신뢰도": 0.98,
        })

    return pd.DataFrame(records)



def find_nearest_apt2_complex(text: str, position: int) -> Optional[str]:
    """Find the closest apartment-card heading before an apt2 transaction."""
    prefix_lines = text[:position].splitlines()
    ignored = {
        "아파트", "전세", "월세", "분양권", "오피스텔", "빌라",
        "일별실거래", "주간실거래", "월별실거래",
    }
    for line in reversed(prefix_lines[-80:]):
        candidate = line.strip()
        if not candidate or candidate in ignored:
            continue
        match = re.fullmatch(
            r"(?P<name>[0-9A-Za-z가-힣().·\-\s]{2,80}?)(?:분양권|아파트|오피스텔)",
            candidate,
        )
        if match:
            return match.group("name").strip()
    return None

def parse_tx_apt2_summary(
    text: str,
    expected_complex: Optional[str] = None,
) -> pd.DataFrame:
    """
    Format 2, including a page that has multiple apartment cards.
    Only a card whose preceding context matches expected_complex is accepted.
    """
    text = normalize_raw_text(text)
    if "계약" not in text or not re.search(r"(?:㎡|m2|m²)", text):
        return pd.DataFrame()

    pattern = re.compile(
        rf"(?P<price>{EOK_AMOUNT_PATTERN})"
        rf"(?P<middle>.{{0,180}}?)"
        rf"(?P<area>\d+(?:\.\d+)?)(?:㎡|m2|m²)"
        rf"(?P<rest>.{{0,120}}?)"
        rf"(?P<floor>\d{{1,2}})층\s*"
        rf"(?P<deal>중개거래|직거래)"
        rf".{{0,80}}?"
        rf"(?P<date>\d{{2}}\.\d{{2}}\.\d{{2}})\s*계약",
        re.S,
    )

    records = []
    for match in pattern.finditer(text):
        if expected_complex:
            nearest_complex = find_nearest_apt2_complex(text, match.start())
            if nearest_complex:
                if not is_same_complex(nearest_complex, expected_complex):
                    continue
            else:
                context = text[max(0, match.start() - 1200):match.start()]
                expected_key = canonical_complex_name(expected_complex)
                if expected_key not in canonical_complex_name(context):
                    continue

        yy, month, day = map(int, match.group("date").split("."))
        year = 2000 + yy
        if not is_valid_date(year, month, day):
            continue

        records.append({
            "날짜": f"{year:04d}.{month:02d}.{day:02d}",
            "타입": normalize_type(match.group("area")),
            "전용면적(㎡)": float(match.group("area")),
            "금액_문자열": match.group("price").strip(),
            "층": match.group("floor"),
            "동": "동미상",
            "거래유형": match.group("deal"),
            "권리유형": "분양권" if "분양권" in text[max(0, match.start()-500):match.end()] else "매매",
            "데이터구분": "실거래",
            "파서형식": "실거래_아파트미요약",
            "파싱신뢰도": 0.90,
        })

    return pd.DataFrame(records)


def parse_transactions(
    text: str,
    expected_complex: Optional[str] = None,
) -> pd.DataFrame:
    frames = [
        parse_tx_naver_table(text),
        parse_tx_vertical_list(text),
        parse_tx_apt2_summary(text, expected_complex),
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True, sort=False)
    result = process_price_columns(result)
    keys = [
        key for key in
        ["날짜", "타입", "층", "금액_하한(억)", "거래유형"]
        if key in result.columns
    ]
    return result.drop_duplicates(subset=keys, keep="first").reset_index(drop=True)


def parse_all_real_estate_text(
    raw_text: str,
    expected_complex: Optional[str] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict]:
    """
    One input -> transaction / sale listing / rental listing.
    Classification is based on each record, not the whole pasted page.
    """
    property_df, property_report = parse_naver_property_cards(
        raw_text,
        expected_complex=expected_complex,
    )

    if property_df.empty:
        sale_df = pd.DataFrame()
        rental_df = pd.DataFrame()
    else:
        sale_df = property_df[property_df["거래구분"] == "매매"].copy()
        rental_df = property_df[property_df["거래구분"].isin(["전세", "월세"])].copy()

        if not sale_df.empty:
            sale_df = process_price_columns(sale_df)

    tx_df = parse_transactions(raw_text, expected_complex=expected_complex)

    detected = []
    if not tx_df.empty:
        detected.append("실거래")
    if not sale_df.empty:
        detected.append("매매호가")
    if not rental_df.empty:
        detected.append("전월세")

    report = {
        **property_report,
        "detected_types": detected,
        "transaction_count": len(tx_df),
        "sale_listing_count": len(sale_df),
        "rental_listing_count": len(rental_df),
        "unparsed": not detected,
    }
    return tx_df, sale_df, rental_df, report


# ============================================================
# 2초 자동 스크리닝 / 확인 모달 / HUD
# ============================================================

PREFIX = "smart_ingest_"


def state_key(name: str) -> str:
    return f"{PREFIX}{name}"


def init_ingest_state() -> None:
    defaults = {
        "input_version": 0,
        "active_widget_key": "",
        "complex_name": "",
        "changed_at": 0.0,
        "last_screened_hash": "",
        "screening_status": "idle",
        "confirmation_pending": False,
        "preview_tx": pd.DataFrame(),
        "preview_sale": pd.DataFrame(),
        "preview_rental": pd.DataFrame(),
        "preview_report": {},
        "preview_raw_text": "",
        "last_hud": None,
        "pending_toast": None,
    }
    for name, value in defaults.items():
        if state_key(name) not in st.session_state:
            st.session_state[state_key(name)] = value


def mark_input_changed(widget_key: str) -> None:
    raw_text = str(st.session_state.get(widget_key, "") or "")
    st.session_state[state_key("changed_at")] = time.time()
    st.session_state[state_key("screening_status")] = (
        "waiting" if raw_text.strip() else "idle"
    )
    st.session_state[state_key("confirmation_pending")] = False


def detected_screening_categories(
    tx_df: pd.DataFrame,
    sale_df: pd.DataFrame,
    rental_df: pd.DataFrame,
) -> list[str]:
    categories = []
    if not tx_df.empty:
        categories.append("실거래가")
    if not sale_df.empty:
        categories.append("매매호가")
    if not rental_df.empty:
        categories.append("전월세")
    return categories


def screening_question(categories: list[str]) -> str:
    if categories == ["실거래가"]:
        return "실거래가 내용이 맞습니까?"
    if categories == ["매매호가"]:
        return "매매호가 내용이 맞습니까?"
    if categories == ["전월세"]:
        return "전월세 내용이 맞습니까?"
    if categories:
        return "여러 데이터 유형이 함께 포함된 내용이 맞습니까?"
    return "자동 분류에 실패했습니다. 원문 형식을 확인해 주세요."


def reset_ingest_preview(*, keep_text: bool) -> None:
    st.session_state[state_key("confirmation_pending")] = False
    st.session_state[state_key("screening_status")] = "idle"
    st.session_state[state_key("preview_tx")] = pd.DataFrame()
    st.session_state[state_key("preview_sale")] = pd.DataFrame()
    st.session_state[state_key("preview_rental")] = pd.DataFrame()
    st.session_state[state_key("preview_report")] = {}
    st.session_state[state_key("preview_raw_text")] = ""

    if not keep_text:
        widget_key = st.session_state.get(state_key("active_widget_key"), "")
        if widget_key and widget_key in st.session_state:
            del st.session_state[widget_key]
        st.session_state[state_key("input_version")] += 1
        st.session_state[state_key("last_screened_hash")] = ""


@st.fragment(run_every=0.5)
def debounced_screening() -> None:
    widget_key = st.session_state.get(state_key("active_widget_key"), "")
    if not widget_key:
        return

    raw_text = str(st.session_state.get(widget_key, "") or "")
    if not raw_text.strip():
        st.session_state[state_key("screening_status")] = "idle"
        return

    current_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    last_hash = st.session_state.get(state_key("last_screened_hash"), "")
    changed_at = float(
        st.session_state.get(state_key("changed_at"), 0.0) or 0.0
    )
    elapsed = time.time() - changed_at

    if current_hash == last_hash:
        return

    if elapsed < 2.0:
        st.caption(
            f"🔎 입력 안정화 확인 중… "
            f"{max(0.0, 2.0 - elapsed):.1f}초"
        )
        return

    complex_name = st.session_state.get(state_key("complex_name"), "")
    st.session_state[state_key("screening_status")] = "parsing"

    tx_df, sale_df, rental_df, report = parse_all_real_estate_text(
        raw_text,
        expected_complex=complex_name or None,
    )

    st.session_state[state_key("preview_tx")] = tx_df
    st.session_state[state_key("preview_sale")] = sale_df
    st.session_state[state_key("preview_rental")] = rental_df
    st.session_state[state_key("preview_report")] = report
    st.session_state[state_key("preview_raw_text")] = raw_text
    st.session_state[state_key("last_screened_hash")] = current_hash
    st.session_state[state_key("screening_status")] = "ready"
    st.session_state[state_key("confirmation_pending")] = True
    st.rerun()


def save_selected_categories(
    selected_categories: Iterable[str],
    complex_name: str,
    raw_text: str,
) -> tuple[bool, dict[str, int], list[str]]:
    selected = set(selected_categories)
    tx_df = st.session_state[state_key("preview_tx")].copy()
    sale_df = st.session_state[state_key("preview_sale")].copy()
    rental_df = st.session_state[state_key("preview_rental")].copy()

    counts = {"실거래가": 0, "매매호가": 0, "전월세": 0}
    errors: list[str] = []

    try:
        if "실거래가" in selected:
            if tx_df.empty:
                errors.append("실거래가로 파싱된 레코드가 없습니다.")
            else:
                counts["실거래가"] = upsert_dataframe(
                    tx_df,
                    complex_name,
                    CATEGORY_TX,
                )
                save_raw_input(raw_text, complex_name, "실거래")

        if "매매호가" in selected:
            if sale_df.empty:
                errors.append("매매호가로 파싱된 레코드가 없습니다.")
            else:
                counts["매매호가"] = upsert_dataframe(
                    sale_df,
                    complex_name,
                    CATEGORY_SALE,
                )
                save_raw_input(raw_text, complex_name, "매매호가")

        if "전월세" in selected:
            if rental_df.empty:
                errors.append("전월세로 파싱된 레코드가 없습니다.")
            else:
                counts["전월세"] = upsert_dataframe(
                    rental_df,
                    complex_name,
                    CATEGORY_RENTAL,
                )
                save_raw_input(raw_text, complex_name, "전월세")

    except Exception as exc:
        errors.append(str(exc))

    success = bool(selected) and sum(counts.values()) > 0 and not errors
    return success, counts, errors


@st.dialog("1차 데이터 스크리닝", width="large")
def confirmation_dialog() -> None:
    tx_df = st.session_state[state_key("preview_tx")]
    sale_df = st.session_state[state_key("preview_sale")]
    rental_df = st.session_state[state_key("preview_rental")]
    report = st.session_state.get(state_key("preview_report"), {})
    raw_text = st.session_state.get(state_key("preview_raw_text"), "")
    complex_name = st.session_state.get(state_key("complex_name"), "")

    detected = detected_screening_categories(
        tx_df,
        sale_df,
        rental_df,
    )

    st.subheader(screening_question(detected))
    st.caption(f"저장 대상 단지: {complex_name or '단지 미지정'}")

    m1, m2, m3 = st.columns(3)
    m1.metric("실거래가", f"{len(tx_df)}건")
    m2.metric("매매호가", f"{len(sale_df)}건")
    m3.metric("전월세", f"{len(rental_df)}건")

    foreign = report.get("foreign_complexes") or []
    rejected = int(report.get("listing_card_rejected", 0) or 0)

    if foreign:
        st.warning(
            "다른 단지로 판단되어 제외: "
            + ", ".join(map(str, foreign))
        )
    if rejected:
        st.warning(
            f"매물 후보 중 {rejected}건은 필수 항목 부족으로 제외됩니다."
        )

    if not detected:
        st.error(
            "파싱에 성공한 레코드가 없습니다. "
            "원문을 수정한 뒤 다시 시도해 주세요."
        )

    selected = st.multiselect(
        "저장할 데이터 유형",
        options=SCREENING_OPTIONS,
        default=detected,
        placeholder="저장할 유형을 선택하세요.",
    )

    tab_tx, tab_sale, tab_rental = st.tabs(
        [
            f"실거래 {len(tx_df)}",
            f"매매 {len(sale_df)}",
            f"전월세 {len(rental_df)}",
        ]
    )
    with tab_tx:
        if tx_df.empty:
            st.info("파싱된 실거래가가 없습니다.")
        else:
            st.dataframe(
                tx_df.head(30),
                use_container_width=True,
                hide_index=True,
            )
    with tab_sale:
        if sale_df.empty:
            st.info("파싱된 매매호가가 없습니다.")
        else:
            st.dataframe(
                sale_df.head(30),
                use_container_width=True,
                hide_index=True,
            )
    with tab_rental:
        if rental_df.empty:
            st.info("파싱된 전월세가 없습니다.")
        else:
            st.dataframe(
                rental_df.head(30),
                use_container_width=True,
                hide_index=True,
            )

    c_save, c_edit, c_cancel = st.columns([1.6, 1, 1])

    with c_save:
        if st.button(
            "✅ 예, 선택한 유형으로 저장",
            type="primary",
            use_container_width=True,
            disabled=not bool(detected),
        ):
            ok, counts, errors = save_selected_categories(
                selected,
                complex_name,
                raw_text,
            )

            if ok:
                total = sum(counts.values())
                st.session_state[state_key("last_hud")] = {
                    "time": datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "complex_name": complex_name,
                    "total": total,
                    "tx": counts["실거래가"],
                    "sale": counts["매매호가"],
                    "rental": counts["전월세"],
                    "types": [
                        key
                        for key, value in counts.items()
                        if value > 0
                    ],
                }
                st.session_state[state_key("pending_toast")] = (
                    f"{complex_name} 총 {total}건 저장 완료"
                )
                reset_ingest_preview(keep_text=False)
                st.rerun()
            else:
                st.error(
                    "저장에 실패했습니다. 입력 원문은 유지됩니다."
                )
                for error in errors:
                    st.write(f"- {error}")

    with c_edit:
        if st.button("✏️ 원문 수정", use_container_width=True):
            reset_ingest_preview(keep_text=True)
            st.rerun()

    with c_cancel:
        if st.button("취소", use_container_width=True):
            reset_ingest_preview(keep_text=True)
            st.rerun()


def render_last_ingest_hud() -> None:
    hud = st.session_state.get(state_key("last_hud"))
    if not hud:
        return

    type_text = " · ".join(hud["types"])
    st.markdown(
        f"""
        <div class="hud-card">
            <div style="font-size:.78rem;color:#475569;">
                최근 입력 완료
            </div>
            <div style="
                font-size:1.05rem;
                font-weight:800;
                color:#14532d;
                margin-top:2px;
            ">
                ✅ {hud["complex_name"]} · 총 {hud["total"]}건
            </div>
            <div style="
                font-size:.88rem;
                color:#334155;
                margin-top:5px;
            ">
                {type_text}<br>
                실거래 {hud["tx"]} ·
                매매 {hud["sale"]} ·
                전월세 {hud["rental"]}
            </div>
            <div style="
                font-size:.74rem;
                color:#64748b;
                margin-top:5px;
            ">
                {hud["time"]}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_smart_input_panel(default_complex_name: str) -> None:
    init_ingest_state()

    toast_message = st.session_state.get(
        state_key("pending_toast")
    )
    if toast_message:
        st.toast(toast_message, icon="✅", duration=6)
        st.session_state[state_key("pending_toast")] = None

    render_last_ingest_hud()

    st.subheader("📥 통합 스마트 입력")
    st.caption(
        "매매·전세·월세·실거래가를 구분하지 않고 붙여넣으세요. "
        "입력값이 서버에 반영된 뒤 2초 후 자동 스크리닝합니다."
    )

    complex_name = st.text_input(
        "🏢 저장 대상 단지",
        value=default_complex_name,
        key=state_key("complex_input"),
    )
    st.session_state[state_key("complex_name")] = complex_name

    version = st.session_state[state_key("input_version")]
    widget_key = f"{PREFIX}raw_text_{version}"
    st.session_state[state_key("active_widget_key")] = widget_key

    st.text_area(
        "원문 입력",
        height=260,
        key=widget_key,
        placeholder=(
            "네이버 매물, 전세·월세, 네이버 실거래가 표, "
            "아파트미 세로형 목록 등을 그대로 붙여넣으세요."
        ),
        help=(
            "붙여넣은 뒤 Ctrl+Enter를 누르거나 입력창 밖을 "
            "클릭하면 2초 자동 판별이 시작됩니다."
        ),
        on_change=mark_input_changed,
        args=(widget_key,),
    )

    status = st.session_state.get(
        state_key("screening_status"),
        "idle",
    )
    if status == "waiting":
        st.info("⏳ 입력이 끝났는지 2초 동안 확인하고 있습니다.")
    elif status == "parsing":
        st.info("🔎 데이터 유형과 레코드를 분석하고 있습니다.")

    debounced_screening()

    if st.session_state.get(
        state_key("confirmation_pending"),
        False,
    ):
        confirmation_dialog()


# ============================================================
# Sidebar
# ============================================================

with st.sidebar:
    st.title("🏙️ 대시보드 제어")

    with st.expander("🔐 관리자 인증", expanded=False):
        render_admin_login()

    connected, connection_message = test_supabase_connection()
    if connected:
        st.caption("🟢 Supabase 연결됨")
    else:
        st.error("Supabase 연결 또는 테이블 설정이 필요합니다.")
        with st.expander("연결 오류 및 초기 SQL"):
            st.code(connection_message)
            st.code(SCHEMA_SQL, language="sql")

    known_complexes = (
        load_all_complex_names()
        if connected
        else []
    )
    default_complex = (
        known_complexes[0]
        if known_complexes
        else "범어자이(주상복합)"
    )

    if is_admin_authenticated() and connected:
        with st.popover(
            "⚙️ 스마트 데이터 입력 & DB 정제",
            use_container_width=True,
        ):
            render_smart_input_panel(default_complex)

            st.markdown("---")
            st.subheader("🛠️ DB 데이터 관리")

            if st.button(
                "↺ 최근 스냅샷 롤백",
                use_container_width=True,
            ):
                ok, message = rollback_latest_snapshot()
                if ok:
                    st.success(message)
                    st.rerun()
                else:
                    st.error(message)

            delete_candidates = (
                load_all_complex_names()
                if connected
                else []
            )
            delete_target = st.selectbox(
                "삭제할 단지",
                delete_candidates or ["없음"],
                key="delete_target",
            )
            if st.button(
                "🗑️ 선택 단지 DB 완전 삭제",
                use_container_width=True,
            ):
                if delete_target != "없음":
                    if delete_complex_data(delete_target):
                        st.success(
                            f"[{delete_target}] 데이터를 삭제했습니다."
                        )
                        st.rerun()
                    else:
                        st.error("단지 데이터 삭제에 실패했습니다.")
    elif connected:
        st.info("데이터 입력·삭제는 관리자 로그인 후 사용할 수 있습니다.")

    st.markdown("---")

    known_complexes = (
        load_all_complex_names()
        if connected
        else []
    )
    selected_complex = st.selectbox(
        "🏢 분석 단지 선택",
        known_complexes or ["범어자이(주상복합)"],
    )

    st.markdown("---")
    st.subheader("📥 DB 다운로드 센터")

    if connected:
        export_ls = load_category_df(
            selected_complex,
            CATEGORY_SALE,
        )
        export_rn = load_category_df(
            selected_complex,
            CATEGORY_RENTAL,
        )
        export_tx = load_category_df(
            selected_complex,
            CATEGORY_TX,
        )

        if not export_ls.empty:
            st.download_button(
                "📥 매매 DB (CSV)",
                data=dataframe_csv_bytes(export_ls),
                file_name=(
                    f"{selected_complex}_listings.csv"
                ),
                mime="text/csv",
                use_container_width=True,
            )
        if not export_rn.empty:
            st.download_button(
                "📥 전월세 DB (CSV)",
                data=dataframe_csv_bytes(export_rn),
                file_name=(
                    f"{selected_complex}_rentals.csv"
                ),
                mime="text/csv",
                use_container_width=True,
            )
        if not export_tx.empty:
            st.download_button(
                "📥 실거래 DB (CSV)",
                data=dataframe_csv_bytes(export_tx),
                file_name=(
                    f"{selected_complex}_transactions.csv"
                ),
                mime="text/csv",
                use_container_width=True,
            )


# ============================================================
# Main dashboard
# ============================================================

st.title(
    f"🏙️ {selected_complex} 정밀 라이프사이클 V29.0"
)

if not connected:
    st.error(
        "Supabase 연결이 완료되지 않았습니다. "
        "Secrets와 초기 SQL을 설정해 주세요."
    )
    st.stop()

ls_df = load_category_df(
    selected_complex,
    CATEGORY_SALE,
)
tx_df = load_category_df(
    selected_complex,
    CATEGORY_TX,
)
rn_df = load_category_df(
    selected_complex,
    CATEGORY_RENTAL,
)
raw_df = load_category_df(
    selected_complex,
    CATEGORY_RAW,
)

target_ls = ls_df.copy()
target_tx = tx_df.copy()
target_rn = rn_df.copy()

has_data = (
    not target_ls.empty
    or not target_tx.empty
    or not target_rn.empty
)

if has_data:
    today_ls = pd.DataFrame()

    if not target_ls.empty:
        for column in [
            "수집일",
            "매물등록일",
            "금액_하한(억)",
        ]:
            if column not in target_ls.columns:
                target_ls[column] = pd.NA

        target_ls["수집일_dt"] = pd.to_datetime(
            target_ls["수집일"],
            errors="coerce",
        )
        latest_dt = target_ls["수집일_dt"].max()
        today_ls = target_ls[
            target_ls["수집일_dt"] == latest_dt
        ].copy()

        today_ls["매물등록일_dt"] = pd.to_datetime(
            today_ls["매물등록일"],
            errors="coerce",
        )
        today_ls["DOM(일)"] = (
            today_ls["수집일_dt"]
            - today_ls["매물등록일_dt"]
        ).dt.days.fillna(0).astype(int)

        today_ls["층_구분"] = (
            today_ls["층"]
            .apply(categorize_floor)
        )
        today_ls["타입_그룹"] = (
            today_ls["타입"]
            .astype(str)
            .str.extract(r"(\d+)")[0]
        )

        price_cuts = []
        first_prices = []

        for _, row in today_ls.iterrows():
            hist = target_ls[
                (target_ls["동"] == row["동"])
                & (target_ls["타입"] == row["타입"])
                & (target_ls["층"] == row["층"])
            ]
            if len(hist) > 1:
                first_price = (
                    hist.sort_values("수집일_dt")
                    .iloc[0]["금액_하한(억)"]
                )
                difference = (
                    row["금액_하한(억)"] - first_price
                )
                price_cuts.append(difference)
                first_prices.append(first_price)
            else:
                price_cuts.append(0.0)
                first_prices.append(
                    row["금액_하한(억)"]
                )

        today_ls["가격변동액(억)"] = price_cuts
        today_ls["최초호가(억)"] = first_prices

        if not target_tx.empty:
            target_tx["타입_그룹"] = (
                target_tx["타입"]
                .astype(str)
                .str.extract(r"(\d+)")[0]
            )
            target_tx["층_구분"] = (
                target_tx["층"]
                .apply(categorize_floor)
            )

            tx_floor_summary = (
                target_tx
                .groupby(
                    ["타입_그룹", "층_구분"]
                )["금액_하한(억)"]
                .mean()
                .reset_index()
                .rename(
                    columns={
                        "금액_하한(억)":
                        "층별실거래평균(억)"
                    }
                )
            )

            tx_type_summary = (
                target_tx
                .groupby("타입_그룹")[
                    "금액_하한(억)"
                ]
                .mean()
                .reset_index()
                .rename(
                    columns={
                        "금액_하한(억)":
                        "타입실거래평균(억)"
                    }
                )
            )

            today_ls = pd.merge(
                today_ls,
                tx_floor_summary,
                on=["타입_그룹", "층_구분"],
                how="left",
            )
            today_ls = pd.merge(
                today_ls,
                tx_type_summary,
                on="타입_그룹",
                how="left",
            )

            today_ls["최근실거래평균(억)"] = (
                today_ls[
                    "층별실거래평균(억)"
                ]
                .fillna(
                    today_ls[
                        "타입실거래평균(억)"
                    ]
                )
            )
            today_ls["층보정_괴리율(%)"] = (
                (
                    today_ls["금액_하한(억)"]
                    - today_ls[
                        "최근실거래평균(억)"
                    ]
                )
                / today_ls[
                    "최근실거래평균(억)"
                ]
            ) * 100
        else:
            today_ls["최근실거래평균(억)"] = np.nan
            today_ls["층보정_괴리율(%)"] = np.nan

    metric1, metric2, metric3, metric4 = st.columns(4)

    min_price = (
        today_ls["금액_하한(억)"].min()
        if not today_ls.empty
        else np.nan
    )

    if not target_tx.empty:
        tx_for_latest = target_tx.copy()
        tx_for_latest["날짜_dt"] = pd.to_datetime(
            tx_for_latest["날짜"],
            errors="coerce",
        )
        tx_for_latest = tx_for_latest.sort_values(
            "날짜_dt"
        )
        latest_tx_price = (
            tx_for_latest.iloc[-1][
                "금액_하한(억)"
            ]
            if not tx_for_latest.empty
            else np.nan
        )
    else:
        latest_tx_price = np.nan

    average_spread = (
        today_ls["층보정_괴리율(%)"].mean()
        if (
            not today_ls.empty
            and "층보정_괴리율(%)"
            in today_ls.columns
        )
        else np.nan
    )
    long_dom_count = (
        len(today_ls[today_ls["DOM(일)"] >= 60])
        if not today_ls.empty
        else 0
    )

    metric1.metric(
        "🏆 매매 최저 호가",
        (
            f"{min_price:.2f} 억"
            if pd.notnull(min_price)
            else "N/A"
        ),
    )
    metric2.metric(
        "📑 최근 실거래가",
        (
            f"{latest_tx_price:.2f} 억"
            if pd.notnull(latest_tx_price)
            else "N/A"
        ),
    )
    metric3.metric(
        "📉 층보정 평균 괴리율",
        (
            f"{average_spread:+.2f}%"
            if pd.notnull(average_spread)
            else "N/A"
        ),
    )
    metric4.metric(
        "⏳ 60일+ 미소진 매물",
        f"{long_dom_count} 건",
        delta=(
            "급매 협상 여지"
            if long_dom_count > 0
            else "매물 소진 양호"
        ),
    )

    st.markdown("---")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        [
            "🎯 실거래 매칭",
            "💬 카톡 브리핑 엑스포트",
            "🔑 전월세 & 정밀 갭 트래킹",
            "📊 괴리율 & 체류기간",
            "📈 시각화 차트",
            "📅 원문 히스토리",
        ]
    )

    with tab1:
        st.markdown(
            f"### 🎯 [{selected_complex}] "
            "최근 실거래가 및 매물 현황"
        )
        left, right = st.columns(2)

        with left:
            st.subheader("📑 최근 실거래가 기록")
            if not target_tx.empty:
                columns = [
                    column
                    for column in [
                        "날짜",
                        "동",
                        "타입",
                        "층",
                        "금액_문자열",
                        "거래유형",
                    ]
                    if column in target_tx.columns
                ]
                st.dataframe(
                    target_tx[columns]
                    .sort_values(
                        "날짜",
                        ascending=False,
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("실거래가 데이터가 없습니다.")

        with right:
            st.subheader("🏡 현재 최신 매매 호가")
            if not today_ls.empty:
                columns = [
                    column
                    for column in [
                        "매물등록일",
                        "동",
                        "타입",
                        "층",
                        "방향",
                        "금액_문자열",
                        "중개사수",
                    ]
                    if column in today_ls.columns
                ]
                st.dataframe(
                    today_ls
                    .sort_values("금액_하한(억)")[
                        columns
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info(
                    "현재 수집된 매매 호가가 없습니다."
                )

    with tab2:
        st.markdown(
            "### 💬 모바일 카카오톡 맞춤 브리핑 리포터"
        )

        if not today_ls.empty:
            latest_dt = (
                today_ls["수집일_dt"].max()
            )
            group_option = st.radio(
                "카톡 브리핑 포맷 선택",
                [
                    "🏢 동별 (기본 - 전체)",
                    "📐 타입별 (전체)",
                    "🧭 방향별 (전체)",
                    "🚀 컴팩트 요약 (최저가 Top 5)",
                ],
                horizontal=True,
            )

            weekday = [
                "월", "화", "수", "목",
                "금", "토", "일",
            ][latest_dt.weekday()]

            briefing = (
                f"📢 [{selected_complex} 오늘 브리핑]\n"
                f"🗓️ 기준: "
                f"{latest_dt.strftime('%m/%d')} "
                f"({weekday})\n"
                f"🏠 총 매물: {len(today_ls)}건\n\n"
            )

            def format_briefing_item(
                row: pd.Series,
                hide_dong: bool = False,
                hide_type: bool = False,
                hide_direction: bool = False,
            ) -> str:
                raw_floor = str(row["층"]).split("/")[0]
                floor_text = (
                    raw_floor
                    if raw_floor.endswith("층")
                    else f"{raw_floor}층"
                )
                parts = []
                if not hide_dong:
                    parts.append(str(row["동"]))
                if not hide_type:
                    parts.append(str(row["타입"]))
                parts.append(floor_text)
                parts.append(
                    str(row["금액_문자열"]).strip()
                )
                if not hide_direction:
                    parts.append(str(row["방향"]))
                return "▪️ " + " / ".join(parts)

            if group_option.startswith("🏢"):
                for dong in sorted(
                    today_ls["동"].dropna().unique()
                ):
                    sub = (
                        today_ls[
                            today_ls["동"] == dong
                        ]
                        .sort_values(
                            "금액_하한(억)"
                        )
                    )
                    briefing += f"[🏢 {dong}]\n"
                    for _, row in sub.iterrows():
                        briefing += (
                            format_briefing_item(
                                row,
                                hide_dong=True,
                            )
                            + "\n"
                        )
                    briefing += "\n"

            elif group_option.startswith("📐"):
                for type_code in sorted(
                    today_ls["타입"]
                    .dropna()
                    .unique()
                ):
                    sub = (
                        today_ls[
                            today_ls["타입"]
                            == type_code
                        ]
                        .sort_values(
                            "금액_하한(억)"
                        )
                    )
                    briefing += (
                        f"[📐 {type_code} 타입]\n"
                    )
                    for _, row in sub.iterrows():
                        briefing += (
                            format_briefing_item(
                                row,
                                hide_type=True,
                            )
                            + "\n"
                        )
                    briefing += "\n"

            elif group_option.startswith("🧭"):
                for direction in sorted(
                    today_ls["방향"]
                    .dropna()
                    .unique()
                ):
                    sub = (
                        today_ls[
                            today_ls["방향"]
                            == direction
                        ]
                        .sort_values(
                            "금액_하한(억)"
                        )
                    )
                    briefing += (
                        f"[🧭 {direction}]\n"
                    )
                    for _, row in sub.iterrows():
                        briefing += (
                            format_briefing_item(
                                row,
                                hide_direction=True,
                            )
                            + "\n"
                        )
                    briefing += "\n"

            else:
                briefing += (
                    "🔥 [타입별 최저가 매물 요약]\n"
                )
                type_min_indexes = (
                    today_ls
                    .groupby("타입")[
                        "금액_하한(억)"
                    ]
                    .idxmin()
                )
                for index in type_min_indexes:
                    row = today_ls.loc[index]
                    briefing += (
                        format_briefing_item(row)
                        + "\n"
                    )

                briefing += (
                    "\n🏆 [단지 최저가 Top 5 매물]\n"
                )
                for _, row in (
                    today_ls
                    .sort_values(
                        "금액_하한(억)"
                    )
                    .head(5)
                    .iterrows()
                ):
                    briefing += (
                        format_briefing_item(row)
                        + "\n"
                    )

            st.text_area(
                "📋 아래 텍스트를 복사하여 "
                "카카오톡으로 발송하세요",
                value=briefing,
                height=450,
            )
        else:
            st.info(
                "브리핑을 생성할 매매 호가 "
                "데이터가 없습니다."
            )

    with tab3:
        st.markdown(
            f"### 🔑 [{selected_complex}] "
            "전월세 시세 & 층수그룹 정밀 갭 분석"
        )

        if not target_rn.empty:
            target_rn["수집일_dt"] = pd.to_datetime(
                target_rn["수집일"],
                errors="coerce",
            )
            latest_rental_date = (
                target_rn["수집일_dt"].max()
            )
            today_rental = target_rn[
                target_rn["수집일_dt"]
                == latest_rental_date
            ].copy()

            today_rental["층_구분"] = (
                today_rental["층"]
                .apply(categorize_floor)
            )
            jeonse_df = today_rental[
                today_rental["거래구분"]
                == "전세"
            ].copy()

            if (
                not jeonse_df.empty
                and not today_ls.empty
            ):
                st.subheader(
                    "🎯 [층수 그룹 통제] "
                    "실전 체결 가능 갭 Matrix"
                )

                jeonse_df["타입_그룹"] = (
                    jeonse_df["타입"]
                    .astype(str)
                    .str.extract(r"(\d+)")[0]
                )

                sale_floor_min = (
                    today_ls
                    .groupby(
                        ["타입_그룹", "층_구분"]
                    )["금액_하한(억)"]
                    .min()
                    .reset_index()
                    .rename(
                        columns={
                            "금액_하한(억)":
                            "매매최저가(억)"
                        }
                    )
                )

                jeonse_floor_max = (
                    jeonse_df
                    .groupby(
                        ["타입_그룹", "층_구분"]
                    )["보증금(억)"]
                    .max()
                    .reset_index()
                    .rename(
                        columns={
                            "보증금(억)":
                            "전세최고가(억)"
                        }
                    )
                )

                gap_df = pd.merge(
                    sale_floor_min,
                    jeonse_floor_max,
                    on=[
                        "타입_그룹",
                        "층_구분",
                    ],
                    how="inner",
                )
                gap_df["실투자갭(억)"] = (
                    gap_df["매매최저가(억)"]
                    - gap_df["전세최고가(억)"]
                )
                gap_df["전세가율(%)"] = (
                    gap_df["전세최고가(억)"]
                    / gap_df["매매최저가(억)"]
                ) * 100

                st.dataframe(
                    gap_df,
                    column_config={
                        "타입_그룹": "타입",
                        "층_구분": "층수 그룹",
                        "매매최저가(억)":
                            st.column_config.NumberColumn(
                                "동일층 매매최저",
                                format="%.2f 억",
                            ),
                        "전세최고가(억)":
                            st.column_config.NumberColumn(
                                "동일층 전세최고",
                                format="%.2f 억",
                            ),
                        "실투자갭(억)":
                            st.column_config.NumberColumn(
                                "🔑 실전 투자갭",
                                format="%.2f 억",
                            ),
                        "전세가율(%)":
                            st.column_config.NumberColumn(
                                "전세가율",
                                format="%.1f %%",
                            ),
                    },
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.info(
                    "매매 호가와 전세 데이터가 동시에 "
                    "수집되면 갭 Matrix가 산출됩니다."
                )

            st.markdown("---")
            st.subheader(
                "📋 전체 전월세 등록 매물 리스트"
            )
            rental_columns = [
                column
                for column in [
                    "매물등록일",
                    "동",
                    "거래구분",
                    "타입",
                    "층",
                    "방향",
                    "금액_문자열",
                    "중개사수",
                ]
                if column in today_rental.columns
            ]
            st.dataframe(
                today_rental
                .sort_values(
                    "보증금(억)",
                    ascending=False,
                )[rental_columns],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info(
                "등록된 전월세 데이터가 없습니다."
            )

    with tab4:
        st.markdown(
            f"### 📊 [{selected_complex}] "
            "층수보정 괴리율 & 체류기간"
        )

        if not today_ls.empty:
            display_columns = [
                column
                for column in [
                    "동",
                    "타입",
                    "층",
                    "층_구분",
                    "방향",
                    "금액_하한(억)",
                    "최근실거래평균(억)",
                    "층보정_괴리율(%)",
                    "DOM(일)",
                    "중개사명",
                ]
                if column in today_ls.columns
            ]
            display_df = today_ls[
                display_columns
            ].copy()

            st.dataframe(
                display_df.sort_values(
                    "DOM(일)",
                    ascending=False,
                ),
                column_config={
                    "금액_하한(억)":
                        st.column_config.NumberColumn(
                            "현재 호가",
                            format="%.2f 억",
                        ),
                    "최근실거래평균(억)":
                        st.column_config.NumberColumn(
                            "층별 실거래평균",
                            format="%.2f 억",
                        ),
                    "층보정_괴리율(%)":
                        st.column_config.NumberColumn(
                            "층보정 괴리율",
                            format="%+.2f %%",
                        ),
                    "DOM(일)":
                        st.column_config.ProgressColumn(
                            "매물 체류기간",
                            format="%d 일",
                            min_value=0,
                            max_value=120,
                        ),
                },
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info(
                "괴리율 분석을 위한 매매 호가 "
                "데이터가 없습니다."
            )

    with tab5:
        st.markdown(
            "### 📈 정밀 시각화 그래픽스"
        )
        chart_left, chart_right = st.columns(2)

        with chart_left:
            figure = go.Figure()

            if not target_ls.empty:
                daily_listing = (
                    target_ls
                    .groupby("수집일")[
                        "금액_하한(억)"
                    ]
                    .agg(["min", "mean", "max"])
                    .reset_index()
                )
                figure.add_trace(
                    go.Scatter(
                        x=daily_listing["수집일"],
                        y=daily_listing["min"],
                        name="최저 호가",
                    )
                )
                figure.add_trace(
                    go.Scatter(
                        x=daily_listing["수집일"],
                        y=daily_listing["max"],
                        name="최고 호가",
                    )
                )

            if not target_tx.empty:
                tx_valid = (
                    target_tx
                    .dropna(
                        subset=[
                            "날짜",
                            "금액_하한(억)",
                        ]
                    )
                    .sort_values("날짜")
                )
                if not tx_valid.empty:
                    hover_labels = (
                        tx_valid["타입"].astype(str)
                        + " / "
                        + tx_valid["층"].astype(str)
                        + "층 / "
                        + tx_valid[
                            "금액_문자열"
                        ].astype(str)
                    )
                    figure.add_trace(
                        go.Scatter(
                            x=tx_valid["날짜"],
                            y=tx_valid[
                                "금액_하한(억)"
                            ],
                            mode="markers",
                            name="실거래 체결점",
                            marker={
                                "size": 10,
                                "symbol": "diamond",
                            },
                            hovertext=hover_labels,
                        )
                    )

            figure.update_layout(
                title="시계열 호가 밴드 vs 실거래가",
                xaxis_title="날짜",
                yaxis_title="억 원",
                hovermode="x unified",
            )
            st.plotly_chart(
                figure,
                use_container_width=True,
            )

        with chart_right:
            if not today_ls.empty:
                heatmap_data = (
                    today_ls
                    .pivot_table(
                        index="층_구분",
                        columns="타입",
                        values="금액_하한(억)",
                        aggfunc="min",
                    )
                )
                floor_order = [
                    "탑층",
                    "고층",
                    "중층",
                    "저층",
                ]
                heatmap_data = heatmap_data.reindex(
                    [
                        floor
                        for floor in floor_order
                        if floor
                        in heatmap_data.index
                    ]
                )

                if not heatmap_data.empty:
                    heatmap_figure = px.imshow(
                        heatmap_data,
                        labels={
                            "x": "타입",
                            "y": "층수 그룹",
                            "color": "최저 호가(억)",
                        },
                        text_auto=".2f",
                    )
                    heatmap_figure.update_layout(
                        title=(
                            "층수 그룹 x 타입별 "
                            "최저 호가 Matrix"
                        )
                    )
                    st.plotly_chart(
                        heatmap_figure,
                        use_container_width=True,
                    )
                else:
                    st.info(
                        "히트맵 구성 데이터가 부족합니다."
                    )
            else:
                st.info(
                    "매매 호가 데이터가 없어 "
                    "히트맵을 생성할 수 없습니다."
                )

        st.markdown("---")
        st.subheader(
            "📉 개별 매물 호가 인하 궤적"
        )

        if not today_ls.empty:
            cut_df = today_ls[
                today_ls["가격변동액(억)"] < 0
            ].copy()

            if not cut_df.empty:
                cut_df["매물식별"] = (
                    cut_df["동"].astype(str)
                    + " / "
                    + cut_df["타입"].astype(str)
                    + " / "
                    + cut_df["층"].astype(str)
                )
                selected_item = st.selectbox(
                    "호가 인하 매물 선택",
                    cut_df["매물식별"].unique(),
                )
                target_item = cut_df[
                    cut_df["매물식별"]
                    == selected_item
                ].iloc[0]

                first_price = target_item[
                    "최초호가(억)"
                ]
                cut_price = target_item[
                    "가격변동액(억)"
                ]
                current_price = target_item[
                    "금액_하한(억)"
                ]

                waterfall = go.Figure(
                    go.Waterfall(
                        orientation="v",
                        measure=[
                            "absolute",
                            "relative",
                            "total",
                        ],
                        x=[
                            "최초 등록 호가",
                            "가격 인하액",
                            "현재 최종 호가",
                        ],
                        textposition="outside",
                        text=[
                            f"{first_price:.2f}억",
                            f"{cut_price:.2f}억",
                            f"{current_price:.2f}억",
                        ],
                        y=[
                            first_price,
                            cut_price,
                            current_price,
                        ],
                    )
                )
                waterfall.update_layout(
                    title=(
                        f"[{selected_item}] "
                        "가격 인하 워터폴"
                    ),
                    yaxis_title="억 원",
                    showlegend=False,
                )
                st.plotly_chart(
                    waterfall,
                    use_container_width=True,
                )
            else:
                st.info(
                    "과거 수집 대비 호가를 인하한 "
                    "매물이 감지되면 표시됩니다."
                )

    with tab6:
        st.markdown(
            "### 🔍 과거 입력 원문 히스토리"
        )

        if not raw_df.empty:
            raw_df["날짜"] = raw_df[
                "날짜"
            ].astype(str)
            search_date = st.selectbox(
                "날짜 선택",
                sorted(
                    raw_df["날짜"].unique(),
                    reverse=True,
                ),
            )

            raw_columns = st.columns(3)
            raw_types = [
                ("실거래", "RTX (실거래)"),
                ("매매호가", "RLS (매매)"),
                ("전월세", "RRN (전월세)"),
            ]

            for column, (
                raw_type,
                label,
            ) in zip(raw_columns, raw_types):
                with column:
                    subset = raw_df[
                        (raw_df["날짜"] == search_date)
                        & (
                            raw_df["유형"]
                            == raw_type
                        )
                    ]
                    content = (
                        subset["원문"].iloc[-1]
                        if not subset.empty
                        else "기록 없음"
                    )
                    st.text_area(
                        label,
                        content,
                        height=350,
                    )
        else:
            st.info(
                "저장된 원문 히스토리가 없습니다."
            )

else:
    st.info(
        "📌 수집된 데이터가 없습니다. "
        "관리자 로그인 후 스마트 입력창에 원문을 붙여넣으세요."
    )
