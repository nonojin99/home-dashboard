"""관심 매물의 가격 인하/저평가 점수 알림.

필요 Secrets:
- BUYER_LISTINGS_PIN: supabase_listings.sql에 설정한 PIN
- MAIL_USER / MAIL_APP_PASSWORD / MAIL_TO: 기존 메일 설정 재사용

동일한 매물·가격·등급 조합은 listings_alerts_seen.json 캐시에 기록해 반복 발송하지 않는다.
"""
from __future__ import annotations

import html
import re
import json
import os
import sys
import urllib.request
from pathlib import Path

import build
import ingest
import mail

SEEN = Path("listings_alerts_seen.json")


def rpc(name: str, body: dict):
    req = urllib.request.Request(
        f"{ingest.SB_URL}/rest/v1/rpc/{name}",
        data=json.dumps(body).encode(),
        headers={"apikey": ingest.SB_KEY, "Authorization": f"Bearer {ingest.SB_KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def gap(a, b):
    return None if not a or not b else round((a / b - 1) * 100, 1)


def complex_key(c):
    return f"{c.get('sgg_cd')}|{c.get('dong')}|{c.get('jibun')}|{c.get('apt')}"


def attach_buildings(complexes):
    keys = [complex_key(c) for c in complexes]
    if not keys:
        return complexes
    try:
        rows = rpc('building_info_lookup', {'p_keys': keys}) or []
        by = {r.get('complex_key'): r for r in rows}
        for c in complexes:
            c['building'] = by.get(complex_key(c))
            c['building_score'] = (c['building'] or {}).get('quality_score')
    except Exception as e:
        print(f"건축물대장 점수 조회 실패: {e}", file=sys.stderr)
    return complexes


def name_key(s):
    """페이지의 nameKey()와 같은 규칙: 띄어쓰기·기호·'아파트' 꼬리말·숫자 없는 괄호 무시."""
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", lambda m: m.group(0) if re.search(r"\d", m.group(0)) else "", str(s or "").lower())
    s = re.sub(r"아파트$", "", s)
    s = re.sub(r"[\s·.,\-_()\[\]]", "", s)
    return re.sub(r"아파트$", "", s)


def load_aliases():
    try:
        raw = json.loads((Path(__file__).parent / "aliases.json").read_text(encoding="utf-8")).get("aliases", {})
        return {name_key(a): name_key(b) for a, b in raw.items()}
    except Exception as e:
        print(f"aliases.json 읽기 실패: {e}", file=sys.stderr)
        return {}


ALIASES = load_aliases()


AREA_PRESETS = [(83, 86), (55, 65), (72, 78), (95, 105)]


def area_range(x):
    """페이지와 같은 규칙: 면적이 기본 구간 안이면 그 구간, 아니면 ±3㎡."""
    v = float(x.get("area") or 84)
    for lo, hi in AREA_PRESETS:
        if lo <= v <= hi:
            return lo, hi
    a = round(v)
    return a - 3, a + 3


def evaluate(x, complexes):
    k = name_key(x.get("apt"))
    k = ALIASES.get(k, k)
    dong = (x.get("dong") or "").strip()
    keyed = [(name_key(c.get("apt")), c) for c in complexes]
    rows = [c for ck, c in keyed if ck == k and (not dong or c.get("dong") == dong)] or [c for ck, c in keyed if ck == k]
    if not rows and len(k) >= 3:
        rows = [c for ck, c in keyed if (k in ck or ck in k) and (not dong or c.get("dong") == dong)]
    p = int(x.get("price") or 0)
    c = min(rows, key=lambda z: abs((z.get("last_price") or 0) - p)) if rows else None
    fair_gap = gap(p, c.get("med6")) if c else None
    last_gap = gap(p, c.get("last_price")) if c else None
    high_gap = gap(p, c.get("high")) if c else None
    price_score = 50.0
    if fair_gap is not None:
        price_score += max(-25, min(30, -fair_gap * 2.2))
    if last_gap is not None:
        price_score += max(-12, min(15, -last_gap * 1.2))
    if high_gap is not None:
        price_score += max(0, min(10, -high_gap * 0.45))
    price_score = max(0, min(100, round(price_score)))
    building_score = int(c.get('building_score')) if c and c.get('building_score') is not None else None
    score = price_score if building_score is None else round(price_score * 0.70 + building_score * 0.30)
    floor = x.get("floor")
    low_floor = (floor is not None and str(floor) != "" and int(floor) <= 3) or "저층" in (x.get("memo") or "")
    label = ("강한 후보" if score >= 80 else "매수 검토" if score >= 65 else "관찰" if score >= 50 else "비쌈") + (" · 저층" if low_floor else "")
    hist = x.get("history") or []
    first = int(hist[0].get("price") or p) if hist else p
    cut = gap(p, first)
    return {"listing": x, "complex": c, "fair_gap": fair_gap, "last_gap": last_gap, "high_gap": high_gap,
            "price_score": price_score, "building_score": building_score, "score": score, "label": label, "cut": cut}


def main():
    pin = os.environ.get("BUYER_LISTINGS_PIN", "")
    if not pin:
        print("BUYER_LISTINGS_PIN 이 없어 관심 매물 알림을 건너뜁니다.")
        return 0
    try:
        listings = rpc("buyer_listings_list", {"p_pin": pin}) or []
        codes = sorted({c for a in build.AREAS for c in build.as_codes(a[2])})
        by_range = {}  # 매물 면적 ±3㎡ · 최근 24개월 (페이지와 같은 기준)
        for x in listings:
            r = area_range(x)
            if r not in by_range:
                by_range[r] = attach_buildings(rpc("map_complexes", {"p_sgg": codes, "p_dongs": None, "p_area_min": r[0], "p_area_max": r[1],
                                                                     "p_months": 24, "p_limit": 2000}) or [])
    except Exception as e:
        print(f"관심 매물 조회 실패: {e}", file=sys.stderr)
        return 1

    # 관심 단지(목표가): 매물이 아니라 "이 가격이면 산다". 목표가 이하 실거래가 신고되면 알린다.
    watch_hits = []
    for x in [v for v in listings if v.get("source") == "watch"]:
        r = evaluate(x, by_range[area_range(x)])
        c = r["complex"]
        if c and c.get("last_price") and int(c["last_price"]) <= int(x["price"]):
            watch_hits.append((x, c))
    listings = [v for v in listings if v.get("source") != "watch"]
    rows = [evaluate(x, by_range[area_range(x)]) for x in listings]
    # 의미 있는 후보만: 매수 검토 이상 또는 최초가 대비 2%+ 인하
    for r in rows:
        if r["complex"] is None:
            print(f"관심 매물 단지 매칭 실패: {r['listing'].get('apt')} — aliases.json에 실거래 단지명을 추가하세요")
    rows = [r for r in rows if r["score"] >= 65 or (r["cut"] is not None and r["cut"] <= -2)]
    rows.sort(key=lambda r: (r["score"], -(r["fair_gap"] or 999)), reverse=True)
    seen = set(json.loads(SEEN.read_text(encoding="utf-8"))) if SEEN.exists() else set()

    def k(r):
        x = r["listing"]
        return f"{x['id']}|{x['price']}|{r['label']}"

    new = [r for r in rows if k(r) not in seen]
    wkey = lambda x, c: f"watch|{x['id']}|{c.get('last_date')}|{c.get('last_price')}"
    watch_new = [(x, c) for x, c in watch_hits if wkey(x, c) not in seen]
    if not new and not watch_new:
        print(f"관심 매물 알림: 새 조건 충족 없음 (현재 {len(rows)}건)")
        return 0

    th = "padding:6px 8px;border-bottom:1px solid #ddd;text-align:right;white-space:nowrap;"
    trs = []
    lines = []
    for r in new[:20]:
        x = r["listing"]
        fair = "—" if r["fair_gap"] is None else f"{r['fair_gap']:+.1f}%"
        cut = "—" if r["cut"] is None else f"{r['cut']:+.1f}%"
        link = f"<a href='{html.escape(x.get('url') or mail.SITE)}'>{html.escape(x['apt'])}</a>"
        trs.append(f"<tr><td style='{th}text-align:left'>{link}</td><td style='{th}'>{x['price']/10000:.2f}억</td>"
                   f"<td style='{th}'>{fair}</td><td style='{th}'>{cut}</td>"
                   f"<td style='{th}'>{'—' if r['building_score'] is None else str(r['building_score'])+'점'}</td>"
                   f"<td style='{th};font-weight:700'>{r['score']} · {r['label']}</td></tr>")
        lines.append(f"{x['apt']} {x['price']/10000:.2f}억 / 기대가 {fair} / 최초가 {cut} / {r['score']} {r['label']}")
    wh = ""
    if watch_new:
        wh = "<h3>목표가 이하 실거래 발생</h3><ul>" + "".join(
            f"<li>{html.escape(x['apt'])} · 목표가 {int(x['price'])/10000:.2f}억 → 실거래 {int(c['last_price'])/10000:.2f}억 ({c.get('last_date')})</li>" for x, c in watch_new) + "</ul>"
        lines += [f"[목표가] {x['apt']} 목표 {int(x['price'])/10000:.2f}억 / 실거래 {int(c['last_price'])/10000:.2f}억 ({c.get('last_date')})" for x, c in watch_new]
    body = ("<div style='font:14px/1.6 -apple-system,Apple SD Gothic Neo,sans-serif'>" + wh +
            f"<h3>관심 매물 매수 후보 {len(new)}건</h3>"
            f"<table style='border-collapse:collapse;font-size:13px'><tr><th style='{th}text-align:left'>매물</th>"
            f"<th style='{th}'>호가</th><th style='{th}'>기대가 대비</th><th style='{th}'>최초가 대비</th><th style='{th}'>건물</th><th style='{th}'>점수</th></tr>{''.join(trs)}</table>"
            "<p style='font-size:12px;color:#6e6e73'>종합 매수점수는 가격 70% + 건물품질 30% 휴리스틱입니다. 동·층·향·수리·권리관계는 별도 확인이 필요합니다.</p>"
            f"<p><a href='{mail.SITE}'>대시보드 열기</a></p></div>")
    subject = f"[부동산] 관심 매물 후보 {len(new)}건" + (f" · 목표가 도달 {len(watch_new)}건" if watch_new else "")
    rc = mail.send(subject, "\n".join(lines), body)
    if rc == 0:
        SEEN.write_text(json.dumps(sorted(seen | {k(r) for r in new} | {wkey(x, c) for x, c in watch_new}), ensure_ascii=False), encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
