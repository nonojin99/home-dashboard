"""84㎡ 거래 이력을 Supabase에 적재한다.

- 최근 REFRESH_MONTHS개월은 매번 통째로 교체한다 (뒤늦은 해제·신고 반영).
- 그보다 오래된 달은 아직 적재되지 않은 것부터 하루 BACKFILL_PER_RUN개씩 채운다.
"""
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

import build

SB_URL = "https://fpjgjwazqpfiffnsgume.supabase.co"
SB_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZwamdqd2F6cXBmaWZmbnNndW1lIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ1OTczMzEsImV4cCI6MjEwMDE3MzMzMX0.u8M4GypmOadJONNLnYk7PozAXMVqGCaY2pwSysa7mNc"  # 공개용 anon 키 (사이트에도 들어 있음)
START_YM = "201901"
REFRESH_MONTHS = 3
BACKFILL_PER_RUN = int(os.environ.get("BACKFILL_PER_RUN", "24"))


def rpc(name, body):
    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/rpc/{name}",
        data=json.dumps(body).encode(),
        headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or "null")


def loaded_months():
    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/apt_ingest_log?select=sgg_cd,ym&limit=5000",
        headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return {(x["sgg_cd"], x["ym"]) for x in json.load(r)}


def rows_for(code, ym, key):
    rows = []
    for t in build.parse_trades(build.fetch_all(build.TRADE_URL, code, ym, key)):
        if not build.is84(t):
            continue
        raw = "|".join(str(t[k]) for k in ("apt", "dong", "jibun", "area", "floor", "price", "date"))
        rows.append(
            {
                "id": hashlib.md5(f"{code}|{raw}".encode()).hexdigest(),
                "dong": t["dong"],
                "jibun": t["jibun"],
                "apt": t["apt"],
                "build_year": int(t["built"]) if t["built"].isdigit() else None,
                "floor": int(t["floor"]) if t["floor"].lstrip("-").isdigit() else None,
                "price": t["price"],
                "deal_date": t["date"],
            }
        )
    return rows


def all_months(now):
    y, m = int(START_YM[:4]), int(START_YM[4:])
    out = []
    while (y, m) <= (now.year, now.month):
        out.append(f"{y}{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def main():
    key = os.environ.get("DATA_GO_KR_API_KEY")
    token = os.environ.get("TRADE_INGEST_TOKEN")
    if not (key and token):
        print("DATA_GO_KR_API_KEY / TRADE_INGEST_TOKEN 이 없어 적재를 건너뜁니다.")
        return 0
    now = datetime.now(timezone(timedelta(hours=9)))
    codes = sorted({c for a in build.AREAS for c in ([a[2]] if isinstance(a[2], str) else a[2])})
    recent = set(build.months_back(now, REFRESH_MONTHS))
    done = loaded_months()
    todo = [(c, ym) for c in codes for ym in build.months_back(now, REFRESH_MONTHS)]
    old = [(c, ym) for ym in reversed(all_months(now)) for c in codes if ym not in recent and (c, ym) not in done]
    todo += old[:BACKFILL_PER_RUN]
    print(f"적재 대상 {len(todo)}건 (남은 과거 {max(0, len(old) - BACKFILL_PER_RUN)}건)")
    fail = 0
    for code, ym in todo:
        try:
            rows = rows_for(code, ym, key)
            n = rpc("trades_ingest", {"p_token": token, "p_sgg": code, "p_yms": [ym], "p_rows": rows})
            print(f"  {code} {ym}: {n}건")
        except Exception as e:
            fail += 1
            print(f"  {code} {ym} 실패: {e}", file=sys.stderr)
    return 1 if fail > len(todo) // 2 else 0


if __name__ == "__main__":
    sys.exit(main())
