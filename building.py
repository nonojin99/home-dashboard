"""건축HUB 건축물대장 데이터를 관심 단지에 연결해 Supabase에 캐시한다.

필요 환경변수:
- DATA_GO_KR_API_KEY: 공공데이터포털 일반 인증키(Decoding 권장)
- BUYER_LISTINGS_PIN: supabase_buildings.sql 설치 시 사용 중인 buyer PIN

동작:
1) 기존 map_complexes RPC에서 최근 거래 단지 목록을 읽는다.
2) 행정안전부 법정동코드 API로 동 이름 -> bjdongCd를 찾는다.
3) 건축HUB 총괄표제부를 우선 조회하고, 없으면 표제부를 집계한다.
4) building_info_upsert RPC로 Supabase에 저장한다.

건축물대장은 월간 갱신 성격이라 기본 45일 이내 캐시는 다시 요청하지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

import build
import ingest

KST = timezone(timedelta(hours=9))
BLD_BASE = "https://apis.data.go.kr/1613000/BldRgstHubService"
REGION_URL = "https://apis.data.go.kr/1741000/StanReginCd/getStanReginCdList"
STALE_DAYS = int(os.environ.get("BUILDING_STALE_DAYS", "45"))
MAX_PER_RUN = int(os.environ.get("BUILDING_MAX_PER_RUN", "250"))


def _request_json(url: str, params: dict, timeout: int = 60):
    q = urllib.parse.urlencode(params, doseq=True, safe="")
    req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": "home-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"JSON 응답 아님: {raw[:300]}") from e


def rpc(name: str, body: dict):
    req = urllib.request.Request(
        f"{ingest.SB_URL}/rest/v1/rpc/{name}",
        data=json.dumps(body).encode(),
        headers={"apikey": ingest.SB_KEY, "Authorization": f"Bearer {ingest.SB_KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or "null")


def _walk_dicts(x):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from _walk_dicts(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk_dicts(v)


def _api_items(payload: dict) -> list[dict]:
    # 건축HUB / 행안부 API의 wrapper가 조금 달라도 item/row를 찾아낸다.
    out = []
    for d in _walk_dicts(payload):
        if any(k in d for k in ("mgmBldrgstPk", "region_cd", "platPlc")):
            out.append(d)
    # 동일 dict가 중복 순회되는 경우 제거
    seen, uniq = set(), []
    for d in out:
        key = json.dumps(d, ensure_ascii=False, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key); uniq.append(d)
    return uniq


def _to_float(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def _to_int(v):
    try:
        return int(float(str(v).replace(",", "")))
    except Exception:
        return None


def parse_jibun(jibun: str):
    s = (jibun or "").strip().replace("번지", "")
    mountain = s.startswith("산")
    s = s[1:].strip() if mountain else s
    m = re.match(r"^(\d+)(?:-(\d+))?$", s)
    if not m:
        return None
    bun = m.group(1).zfill(4)
    ji = (m.group(2) or "0").zfill(4)
    return ("1" if mountain else "0", bun, ji)


def region_code(key: str, sgg_cd: str, dong: str) -> str | None:
    # 주소명 검색은 현행 시군구명 변경에도 비교적 강하다. 결과는 10자리 코드.
    # locatadd_nm을 동 이름만 넣고 sgg_cd로 추가 필터링한다.
    try:
        loc = (build.SGG_NAMES.get(sgg_cd, "") + " " + dong).strip()
        p = _request_json(REGION_URL, {
            "ServiceKey": key,
            "pageNo": 1,
            "numOfRows": 100,
            "type": "json",
            "locatadd_nm": loc,
        })
        # 일부 시군구 개편 직후에는 전체 주소 검색이 늦게 반영될 수 있어 동명만 한 번 더 시도한다.
        if not _api_items(p):
            p = _request_json(REGION_URL, {
                "ServiceKey": key, "pageNo": 1, "numOfRows": 200, "type": "json", "locatadd_nm": dong
            })
        cand = []
        for d in _api_items(p):
            cd = str(d.get("region_cd") or d.get("locatjijuk_cd") or "").strip()
            nm = str(d.get("locatadd_nm") or "").strip()
            if len(cd) == 10 and cd.startswith(sgg_cd) and dong in nm and cd.endswith("00"):
                cand.append((0 if nm.endswith(dong) else 1, cd, nm))
        if cand:
            cand.sort()
            return cand[0][1][5:10]
    except Exception as e:
        print(f"법정동코드 조회 실패 {sgg_cd} {dong}: {e}", file=sys.stderr)
    return None


def bld_fetch(endpoint: str, key: str, sgg: str, bjdong: str, jibun: str) -> list[dict]:
    pj = parse_jibun(jibun)
    if not pj:
        return []
    plat, bun, ji = pj
    params = {
        "serviceKey": key, "sigunguCd": sgg, "bjdongCd": bjdong,
        "platGbCd": plat, "bun": bun, "ji": ji,
        "_type": "json", "numOfRows": 100, "pageNo": 1,
    }
    p = _request_json(f"{BLD_BASE}/{endpoint}", params)
    # 오류 응답이 HTTP 200으로 오는 경우도 있어 resultCode 확인
    for d in _walk_dicts(p):
        rc = d.get("resultCode")
        if rc is not None and str(rc) not in ("00", "000", "INFO-0"):
            raise RuntimeError(f"건축HUB 오류 {rc}: {d.get('resultMsg') or d.get('resultMsg') or ''}")
    return [d for d in _api_items(p) if d.get("mgmBldrgstPk") or d.get("platPlc")]


def _parking(row: dict) -> int:
    direct = _to_int(row.get("totPkngCnt"))
    if direct is not None:
        return direct
    return sum(_to_int(row.get(k)) or 0 for k in ("indrMechUtcnt", "oudrMechUtcnt", "indrAutoUtcnt", "oudrAutoUtcnt"))


def aggregate(rows: list[dict], source: str) -> dict | None:
    if not rows:
        return None
    # 총괄표제부는 보통 필지 단위 1행. 표제부는 주건축물 중심으로 합산한다.
    mains = [r for r in rows if str(r.get("mainAtchGbCd", "0")) in ("0", "", "None")]
    use = mains or rows
    hh = sum(_to_int(r.get("hhldCnt")) or 0 for r in use)
    park = sum(_parking(r) for r in use)
    elev = sum((_to_int(r.get("rideUseElvtCnt")) or 0) + (_to_int(r.get("emgenUseElvtCnt")) or 0) for r in use)
    floors = max([_to_int(r.get("grndFlrCnt")) or 0 for r in use] or [0])
    vl = [_to_float(r.get("vlRat")) for r in use if (_to_float(r.get("vlRat")) or 0) > 0]
    bc = [_to_float(r.get("bcRat")) for r in use if (_to_float(r.get("bcRat")) or 0) > 0]
    dates = [str(r.get("useAprDay") or "") for r in use if str(r.get("useAprDay") or "").isdigit() and len(str(r.get("useAprDay"))) >= 8]
    seismic_vals = [str(r.get("rserthqkDsgnApplyYn")) for r in use if str(r.get("rserthqkDsgnApplyYn")) not in ("", "None")]
    seismic = None
    if seismic_vals:
        seismic = any(v in ("1", "Y", "y", "true", "True") for v in seismic_vals)
    row0 = rows[0]
    return {
        "source_kind": source,
        "households": hh or None,
        "parking": park or None,
        "parking_per_household": round(park / hh, 2) if hh and park else None,
        "elevators": elev or None,
        "max_floor": floors or None,
        "floor_area_ratio": round(sum(vl) / len(vl), 1) if vl else None,
        "building_coverage_ratio": round(sum(bc) / len(bc), 1) if bc else None,
        "use_approval_date": min(dates) if dates else None,
        "seismic": seismic,
        "main_purpose": str(row0.get("mainPurpsCdNm") or row0.get("etcPurps") or "")[:120],
        "road_address": str(row0.get("newPlatPlc") or "")[:300],
        "raw_count": len(rows),
    }


def quality_score(info: dict | None, build_year: int | None = None) -> int | None:
    if not info:
        return None
    score = 50.0
    pph = info.get("parking_per_household")
    if pph is not None:
        score += max(-14, min(16, (pph - 1.0) * 25))
    hh = info.get("households")
    if hh:
        score += 8 if hh >= 1000 else 5 if hh >= 500 else 2 if hh >= 300 else -2 if hh < 100 else 0
    far = info.get("floor_area_ratio")
    if far:
        score += 5 if far <= 220 else 2 if far <= 280 else -3 if far <= 350 else -7
    if info.get("seismic") is True:
        score += 5
    elif info.get("seismic") is False:
        score -= 3
    elev = info.get("elevators")
    if elev and hh:
        per100 = elev / hh * 100
        score += 4 if per100 >= 0.8 else 1 if per100 >= 0.5 else -3
    year = build_year
    if not year and info.get("use_approval_date"):
        try: year = int(str(info["use_approval_date"])[:4])
        except Exception: year = None
    if year:
        age = datetime.now(KST).year - year
        score += 7 if age <= 10 else 3 if age <= 20 else -3 if age >= 30 else 0
    return max(0, min(100, round(score)))


def cached_keys() -> set[str]:
    try:
        rows = rpc("building_info_stale_keys", {"p_days": STALE_DAYS}) or []
        # 함수는 'fresh' key 목록을 반환한다.
        return {str(x.get("complex_key") or "") for x in rows if x.get("complex_key")}
    except Exception as e:
        print("건축물 캐시 조회 실패:", e, file=sys.stderr)
        return set()


def main() -> int:
    key = os.environ.get("DATA_GO_KR_API_KEY", "")
    pin = os.environ.get("BUYER_LISTINGS_PIN", "")
    if not (key and pin):
        print("DATA_GO_KR_API_KEY / BUYER_LISTINGS_PIN 이 없어 건축물대장 적재를 건너뜁니다.")
        return 0

    codes = sorted({c for a in build.AREAS for c in build.as_codes(a[2])})
    try:
        complexes = rpc("map_complexes", {"p_sgg": codes, "p_dongs": None, "p_area_min": 83, "p_area_max": 86,
                                           "p_months": 12, "p_limit": 4000}) or []
    except Exception as e:
        print("단지 목록 조회 실패:", e, file=sys.stderr)
        return 1
    fresh = cached_keys()
    todo = []
    for c in complexes:
        ck = f"{c.get('sgg_cd')}|{c.get('dong')}|{c.get('jibun')}|{c.get('apt')}"
        if c.get("jibun") and ck not in fresh:
            todo.append((ck, c))
    todo = todo[:MAX_PER_RUN]
    print(f"건축물대장 갱신 대상 {len(todo)}개 / 최근 거래 단지 {len(complexes)}개")

    dong_cache: dict[tuple[str,str], str | None] = {}
    ok = fail = 0
    for i, (ck, c) in enumerate(todo, 1):
        sgg, dong, jibun = str(c.get("sgg_cd")), str(c.get("dong") or ""), str(c.get("jibun") or "")
        try:
            dk = (sgg, dong)
            if dk not in dong_cache:
                dong_cache[dk] = region_code(key, sgg, dong)
            bjdong = dong_cache[dk]
            if not bjdong:
                raise RuntimeError("법정동코드 미확인")
            rows = bld_fetch("getBrRecapTitleInfo", key, sgg, bjdong, jibun)
            info = aggregate(rows, "recap")
            if not info:
                rows = bld_fetch("getBrTitleInfo", key, sgg, bjdong, jibun)
                info = aggregate(rows, "title")
            if not info:
                raise RuntimeError("건축물대장 결과 없음")
            info.update({
                "complex_key": ck, "sgg_cd": sgg, "bjdong_cd": bjdong,
                "dong": dong, "jibun": jibun, "apt": c.get("apt") or "",
                "quality_score": quality_score(info, _to_int(c.get("build_year"))),
                "fetched_at": datetime.now(KST).isoformat(),
            })
            rpc("building_info_upsert", {"p_pin": pin, "p_row": info})
            ok += 1
            print(f"  {i}/{len(todo)} {c.get('apt')} {dong} {jibun}: {info['quality_score']}점 / {info['source_kind']}")
        except Exception as e:
            fail += 1
            print(f"  {i}/{len(todo)} {c.get('apt')} 실패: {e}", file=sys.stderr)
        time.sleep(0.08)
    print(f"건축물대장 완료: 성공 {ok}, 실패 {fail}")
    return 1 if todo and fail > max(5, len(todo) * 0.7) else 0


if __name__ == "__main__":
    sys.exit(main())
