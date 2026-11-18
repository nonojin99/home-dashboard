"""국토교통부 실거래가를 받아 84㎡ 매수 타이밍 대시보드(site/index.html)를 만든다.

환경변수 DATA_GO_KR_API_KEY 가 필요하다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import summary

TRADE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
RENT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
ECOS_URL = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/200/{stat}/M/{start}/{end}/{item}"

# 한국은행 ECOS 월별 금리: (키, 통계표, 항목)
RATE_SERIES = [
    ("base", "722Y001", "0101000"),  # 한국은행 기준금리
    ("mortgage", "121Y006", "BECBLA0302"),  # 예금은행 주택담보대출 금리(신규취급액 기준)
]
RATE_START = "202101"

MONTHS = 6
PAGE_SIZE = 1000
AREA_MIN, AREA_MAX = 83.0, 86.0  # 전용 84㎡ 범위
RECENT_COUNT = 15
MAP_MONTHS = 3  # 지도에 올릴 84㎡ 거래 기간(이번 달 포함)
MAP_TRADES = 5  # 단지별로 보여줄 최근 거래 수

# (지역 키, 이름, 법정동코드 5자리(두 구에 걸치면 튜플), 포함할 동 목록 또는 None=전체, 선택 목록 묶음)
AREAS = [
    ("dt", "화성 동탄구 전체", "41597", None, "화성 동탄"),
    ("dt1a", "동탄1 시범·솔빛 (반송동)", "41597", ["반송동"], "화성 동탄"),
    ("dt1b", "동탄1 능동·석우동", "41597", ["능동", "석우동"], "화성 동탄"),
    ("station", "동탄역권 (영천·청계·여울·오산동)", "41597", ["영천동", "청계동", "여울동", "오산동"], "화성 동탄"),
    ("lake", "호수공원권 (산척·장지·송동)", "41597", ["산척동", "장지동", "송동"], "화성 동탄"),
    ("mok", "목동·신동", "41597", ["목동", "신동"], "화성 동탄"),
    ("yt", "수원 영통구 전체", "41117", None, "수원"),
    ("ytmp", "영통·망포 (영통·망포·신동)", "41117", ["영통동", "망포동", "신동"], "수원"),
    ("gg", "광교 (이의동·하동)", "41117", ["이의동", "하동"], "수원"),
    ("ytmt", "매탄·원천", "41117", ["매탄동", "원천동"], "수원"),
    ("pd", "수원 팔달구 전체 (수원시청·매교)", "41115", None, "수원"),
    ("hs", "화서·정자 (화서동·정자동)", ("41115", "41111"), ["화서동", "정자동"], "수원"),
    ("gs", "수원 권선구 전체 (권선·세류)", "41113", None, "수원"),
    ("sj", "용인 수지구 전체", "41465", None, "용인 수지"),
    ("sh", "상현 (상현동)", "41465", ["상현동"], "용인 수지"),
    ("os", "오산시 전체", "41370", None, "오산"),
    ("gp", "군포시 전체", "41410", None, "군포 산본"),
    ("sb", "산본 (산본동)", "41410", ["산본동"], "군포 산본"),
    ("ys", "서울 용산구 전체", "11170", None, "서울 용산"),
]

# 지도에서 주소를 좌표로 바꿀 때 앞에 붙일 시·구 이름
SGG_NAMES = {
    "41597": "경기도 화성시 동탄구",
    "41117": "경기도 수원시 영통구",
    "41115": "경기도 수원시 팔달구",
    "41113": "경기도 수원시 권선구",
    "41111": "경기도 수원시 장안구",
    "41465": "경기도 용인시 수지구",
    "41370": "경기도 오산시",
    "41410": "경기도 군포시",
    "11170": "서울특별시 용산구",
}

KST = timezone(timedelta(hours=9))


def as_codes(code) -> tuple[str, ...]:
    return (code,) if isinstance(code, str) else tuple(code)


def months_back(now: datetime, n: int) -> list[str]:
    """이번 달부터 n개월, 최신순 YYYYMM 목록."""
    out = []
    y, m = now.year, now.month
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def fetch(url: str, region: str, ym: str, page: int, key: str) -> str:
    query = urllib.parse.urlencode(
        {"LAWD_CD": region, "DEAL_YMD": ym, "numOfRows": PAGE_SIZE, "pageNo": page}
    )
    full = f"{url}?serviceKey={urllib.parse.quote(key, safe='')}&{query}"
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(full, timeout=60) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:  # 네트워크 오류는 잠깐 쉬고 재시도
            last_err = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{region} {ym} 요청 실패: {last_err}")


def txt(item: ET.Element, tag: str) -> str:
    return (item.findtext(tag) or "").strip()


def amount(raw: str) -> int | None:
    try:
        return int(raw.replace(",", ""))
    except ValueError:
        return None


def fetch_all(url: str, region: str, ym: str, key: str) -> list[ET.Element]:
    """해제되지 않은 거래 <item> 전부를 페이지를 넘기며 모은다."""
    items: list[ET.Element] = []
    page = 1
    while True:
        root = ET.fromstring(fetch(url, region, ym, page, key))
        code = root.findtext(".//resultCode") or ""
        if code not in ("000", "00"):
            msg = root.findtext(".//resultMsg") or ""
            raise RuntimeError(f"{region} {ym} API 오류 {code}: {msg}")
        page_items = root.findall(".//item")
        items.extend(page_items)
        total = int(root.findtext(".//totalCount") or "0")
        if not page_items or page * PAGE_SIZE >= total:
            break
        page += 1
    return [i for i in items if txt(i, "cdealType") != "O"]


def parse_trades(items: list[ET.Element]) -> list[dict]:
    out = []
    for i in items:
        price = amount(txt(i, "dealAmount"))
        if price is None:
            continue
        out.append(
            {
                "apt": txt(i, "aptNm"),
                "dong": txt(i, "umdNm"),
                "area": float(txt(i, "excluUseAr") or 0),
                "floor": txt(i, "floor"),
                "price": price,
                "date": f"{txt(i, 'dealYear')}-{txt(i, 'dealMonth').zfill(2)}-{txt(i, 'dealDay').zfill(2)}",
                "built": txt(i, "buildYear"),
                "jibun": txt(i, "jibun"),
            }
        )
    return out


def parse_rents(items: list[ET.Element]) -> list[dict]:
    out = []
    for i in items:
        deposit = amount(txt(i, "deposit"))
        if deposit is None:
            continue
        out.append(
            {
                "dong": txt(i, "umdNm"),
                "area": float(txt(i, "excluUseAr") or 0),
                "deposit": deposit,
                "monthly": amount(txt(i, "monthlyRent")) or 0,
            }
        )
    return out


def is84(r: dict) -> bool:
    return AREA_MIN <= r["area"] < AREA_MAX


def eok(v: float | None) -> float | None:
    return None if v is None else round(v / 10000, 2)


def pct(cur: float | None, prev: float | None) -> float | None:
    if cur is None or not prev:
        return None
    return round((cur / prev - 1) * 100, 1)


def summarize(trades: dict[str, list[dict]], rents: dict[str, list[dict]], yms: list[str], dongs):
    keep = (lambda r: True) if dongs is None else (lambda r: r["dong"] in dongs)
    rows = []
    for ym in yms:
        t = [r for r in trades[ym] if keep(r)]
        t84 = [r["price"] for r in t if is84(r)]
        j84 = [r["deposit"] for r in rents[ym] if keep(r) and is84(r) and r["monthly"] == 0]
        sale = eok(statistics.median(t84)) if t84 else None
        jeonse = eok(statistics.median(j84)) if j84 else None
        rows.append(
            {
                "ym": f"{ym[:4]}-{ym[4:]}",
                "trades": len(t),
                "trades84": len(t84),
                "sale": sale,
                "jeonse84": len(j84),
                "jeonse": jeonse,
                "ratio": round(jeonse / sale * 100, 1) if sale and jeonse else None,
            }
        )
    for cur, prev in zip(rows, rows[1:]):
        cur["saleMom"] = pct(cur["sale"], prev["sale"])
        cur["tradesMom"] = pct(cur["trades"], prev["trades"])
        cur["jeonseMom"] = pct(cur["jeonse"], prev["jeonse"])
    recent = sorted(
        (r for ym in yms[:2] for r in trades[ym] if keep(r) and is84(r)),
        key=lambda r: r["date"],
        reverse=True,
    )[:RECENT_COUNT]
    return {
        "months": rows,
        "recent": [
            {"date": r["date"], "apt": r["apt"], "dong": r["dong"], "floor": r["floor"],
             "built": r["built"], "price": eok(r["price"])}
            for r in recent
        ],
    }


def complexes(trades: dict[str, list[dict]], yms: list[str]) -> list[dict]:
    """최근 MAP_MONTHS개월 84㎡ 거래를 단지별로 묶는다."""
    groups: dict[tuple, list[dict]] = {}
    for ym in yms[:MAP_MONTHS]:
        for r in trades[ym]:
            if is84(r) and r["jibun"]:
                groups.setdefault((r["dong"], r["jibun"], r["apt"]), []).append(r)
    out = []
    for (dong, jibun, apt), rows in groups.items():
        rows.sort(key=lambda r: r["date"], reverse=True)
        out.append({
            "apt": apt, "dong": dong, "jibun": jibun, "built": rows[0]["built"], "count": len(rows),
            "trades": [{"date": r["date"], "floor": r["floor"], "price": eok(r["price"])} for r in rows[:MAP_TRADES]],
        })
    out.sort(key=lambda c: (c["dong"], c["apt"]))
    return out


def merge(by_code: dict[str, dict[str, list[dict]]], codes, yms: list[str]) -> dict[str, list[dict]]:
    """여러 구의 월별 거래를 합친다."""
    return {ym: [r for c in codes for r in by_code[c][ym]] for ym in yms}


def fetch_rates(key: str, now: datetime) -> dict | None:
    """ECOS에서 월별 금리를 받는다. 키가 없거나 실패하면 None (금리 없이 페이지는 만든다)."""
    if not key:
        print("ECOS_API_KEY 가 없어 금리는 건너뜁니다")
        return None
    out = {}
    for name, stat, item in RATE_SERIES:
        url = ECOS_URL.format(key=urllib.parse.quote(key, safe=""), stat=stat, item=item,
                              start=RATE_START, end=now.strftime("%Y%m"))
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            rows = body["StatisticSearch"]["row"]
            out[name] = [[f"{r['TIME'][:4]}-{r['TIME'][4:]}", float(r["DATA_VALUE"])] for r in rows]
            print(f"금리 {name}: {len(rows)}개월, 최근 {out[name][-1]}")
        except Exception as e:
            print(f"금리 {name} 실패: {e} / 응답: {str(locals().get('body', ''))[:200]}")
            return None
    return out


def build(key: str, now: datetime) -> dict:
    yms = months_back(now, MONTHS)
    codes = sorted({c for a in AREAS for c in as_codes(a[2])})
    trades: dict[str, dict[str, list[dict]]] = {}
    rents: dict[str, dict[str, list[dict]]] = {}
    for code in codes:
        trades[code], rents[code] = {}, {}
        for ym in yms:
            trades[code][ym] = parse_trades(fetch_all(TRADE_URL, code, ym, key))
            rents[code][ym] = parse_rents(fetch_all(RENT_URL, code, ym, key))
            print(f"{code} {ym}: 매매 {len(trades[code][ym])}건, 전월세 {len(rents[code][ym])}건")
    return {
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "areas": [
            {"key": k, "name": name, "codes": list(as_codes(code)), "dongs": dongs, "group": group,
             **summarize(merge(trades, as_codes(code), yms), merge(rents, as_codes(code), yms), yms, dongs)}
            for k, name, code, dongs, group in AREAS
        ],
        "sggNames": SGG_NAMES,
        "rates": fetch_rates(os.environ.get("ECOS_API_KEY", ""), now),
        "complexes": {code: complexes(trades[code], yms) for code in codes},
    }


def render(data: dict, template: Path, out: Path) -> None:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = template.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")


def main() -> int:
    key = os.environ.get("DATA_GO_KR_API_KEY", "")
    if not key:
        print("DATA_GO_KR_API_KEY 가 없습니다", file=sys.stderr)
        return 1
    here = Path(__file__).parent
    data = build(key, datetime.now(KST))
    now = datetime.now(KST)
    data["summary"] = summary.make(data, now)
    render(data, here / "template.html", here / "site" / "index.html")
    (here / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")  # 메일 요약용 (배포 안 함)
    print("site/index.html 생성 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
