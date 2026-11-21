"""지역별 요약 신호·금리 문구·신고가 대비 하락 단지. 사이트 탭과 메일이 함께 쓴다."""
import json
import math
import sys
import urllib.request
from pathlib import Path

PER_DAY = 120  # ingest.py BACKFILL_PER_RUN 과 같아야 한다
SB_URL = "https://fpjgjwazqpfiffnsgume.supabase.co"
SB_KEY = open(Path(__file__).with_name("ingest.py")).read().split('SB_KEY = "')[1].split('"')[0]


def pct(a, b):
    return None if a is None or not b else round((a - b) / b * 100, 1)


def signal(price3m, trades_vs_avg):
    if price3m is None or trades_vs_avg is None:
        return "자료 부족"
    p = 1 if price3m > 1 else -1 if price3m < -1 else 0
    t = 1 if trades_vs_avg > 10 else -1 if trades_vs_avg < -10 else 0
    if p < 0 and t < 0:
        return "하락 압력"
    if p > 0 and t > 0:
        return "과열 신호"
    if p > 0 and t <= 0:
        return "상승 피로"
    if p < 0 and t > 0:
        return "저가 매수세"
    return "중립"


def area_rows(data):
    rows = []
    for a in data["areas"]:
        m = a["months"]  # 0=이번 달(부분), 1=지난달, 2.. 이전
        if len(m) < 5:
            continue
        last, prev, three = m[1], m[2], m[4]
        avg = sum(x["trades"] for x in m[2:]) / len(m[2:])
        rows.append(
            {
                "name": a["name"], "group": a["group"],
                "sale": last["sale"], "mom": pct(last["sale"], prev["sale"]), "m3": pct(last["sale"], three["sale"]),
                "trades": last["trades"], "tvs": pct(last["trades"], avg), "ratio": last["ratio"],
                "signal": signal(pct(last["sale"], three["sale"]), pct(last["trades"], avg)),
            }
        )
    return rows


def rate_line(rates):
    if not rates:
        return "금리 자료 없음"
    out = []
    for key, label in (("base", "기준금리"), ("mortgage", "주담대 금리")):
        s = rates.get(key) or []
        if len(s) >= 2:
            out.append(f"{label} {s[-1][1]:.2f}% ({s[-1][0]}, 전월 대비 {s[-1][1] - s[-2][1]:+.2f}%p)")
    return " · ".join(out) or "금리 자료 없음"


def sb_get(path, body=None):
    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/{path}", data=json.dumps(body).encode() if body else None,
        headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def history(data, now):
    """거래 이력 적재 진행 상황: (가장 오래된 적재 월, 진행률 %, 남은 일수). 실패하면 None들."""
    try:
        req = urllib.request.Request(
            f"{SB_URL}/rest/v1/apt_ingest_log?select=ym&ver=gte.2&order=ym.asc&limit=1",
            headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Prefer": "count=exact"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            first = json.load(r)
            done = int(r.headers.get("Content-Range", "*/0").split("/")[-1])
        ncodes = len({c for a in data["areas"] for c in a["codes"]})
        expected = ncodes * ((now.year - 2019) * 12 + now.month)
        remain = max(0, expected - done)
        return (first[0]["ym"] if first else None), min(100, round(done * 100 / expected)), math.ceil(remain / PER_DAY)
    except Exception as e:
        print("적재 진행 조회 실패:", e, file=sys.stderr)
        return None, None, None


def drops(data, months=3, min_drop=5, limit=12):
    """84㎡·8~9억·2015년 이후에서 단지별 최근 거래가가 신고가 대비 낮은 순."""
    try:
        codes = sorted({c for a in data["areas"] for c in a["codes"]})
        return sb_get("rpc/search_trades", {"p_sgg": codes, "p_min": 80000, "p_max": 90000, "p_build_min": 2015,
                                            "p_months": months, "p_min_drop": min_drop, "p_limit": limit})
    except Exception as e:
        print("신고가 비교 조회 실패:", e, file=sys.stderr)
        return []


def make(data, now):
    since, progress, eta = history(data, now)
    return {"rows": area_rows(data), "rateLine": rate_line(data.get("rates")), "drops": drops(data), "fresh": drops(data, 1, 10, 12),
            "since": since, "progress": progress, "etaDays": eta}
