"""주간 요약 메일. build.py가 만든 data.json을 읽어 지역별 신호를 정리해 보낸다.

발송 조건: 월요일(KST) 정기 실행, 수동 실행, 또는 커밋 메시지에 [mail]이 있을 때.
"""
import html
import json
import os
import smtplib
import ssl
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

KST = timezone(timedelta(hours=9))
SB_URL = "https://fpjgjwazqpfiffnsgume.supabase.co"
SB_KEY = open(Path(__file__).with_name("ingest.py")).read().split('SB_KEY = "')[1].split('"')[0]
SITE = "https://nonojin99.github.io/home-dashboard/"


def pct(a, b):
    return None if a is None or not b else round((a - b) / b * 100, 1)


def fmt(v, suffix="%"):
    return "—" if v is None else f"{v:+.1f}{suffix}"


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


def drops(data):
    """내 조건(84㎡·8~9억·2015년 이후)에서 최근 거래가가 신고가 대비 낮은 단지."""
    try:
        codes = sorted({c for a in data["areas"] for c in a["codes"]})
        rows = sb_get("rpc/search_trades", {"p_sgg": codes, "p_min": 80000, "p_max": 90000, "p_build_min": 2015,
                                            "p_months": 3, "p_min_drop": 5, "p_limit": 12})
        first = sb_get("apt_ingest_log?select=ym&order=ym.asc&limit=1")
        return rows, (first[0]["ym"] if first else None)
    except Exception as e:
        print("신고가 비교 조회 실패:", e, file=sys.stderr)
        return [], None


def build_mail(data, now):
    rows = area_rows(data)
    drop_rows, since = drops(data)
    th = "padding:6px 8px;border-bottom:1px solid #ddd;text-align:right;white-space:nowrap;"
    td = th + "font-variant-numeric:tabular-nums;"
    sig_color = {"하락 압력": "#1a7f37", "저가 매수세": "#1a7f37", "과열 신호": "#d1242f", "상승 피로": "#d97706"}
    h = [f"<div style='font:14px/1.6 -apple-system,\"Apple SD Gothic Neo\",sans-serif;color:#1d1d1f'>",
         f"<h2 style='margin:0 0 4px'>경기 남부 84㎡ 주간 요약</h2><div style='color:#6e6e73'>{now:%Y-%m-%d} · 지난달 확정 수치 기준</div>",
         f"<p>{html.escape(rate_line(data.get('rates')))}</p>",
         "<table style='border-collapse:collapse;font-size:13px'><tr>" + "".join(
             f"<th style='{th}text-align:left;color:#6e6e73'>{c}</th>" if i == 0 else f"<th style='{th}color:#6e6e73'>{c}</th>"
             for i, c in enumerate(["지역", "중위가", "전월", "3개월", "거래량", "평균 대비", "전세가율", "신호"]))
         + "</tr>"]
    for r in rows:
        c = sig_color.get(r["signal"], "#6e6e73")
        h.append(
            f"<tr><td style='{td}text-align:left'>{html.escape(r['name'])}</td>"
            f"<td style='{td}'>{'—' if r['sale'] is None else f'{r['sale']:.2f}억'}</td><td style='{td}'>{fmt(r['mom'])}</td><td style='{td}'>{fmt(r['m3'])}</td>"
            f"<td style='{td}'>{r['trades']}</td><td style='{td}'>{fmt(r['tvs'])}</td>"
            f"<td style='{td}'>{'—' if r['ratio'] is None else f'{r['ratio']:.1f}%'}</td>"
            f"<td style='{td}color:{c};font-weight:600'>{r['signal']}</td></tr>")
    h.append("</table>")
    h.append("<p style='color:#6e6e73;font-size:12px'>신호 = 중위가 3개월 변화(±1%)와 거래량의 직전 평균 대비(±10%)를 조합한 단순 규칙입니다. "
             "중위가는 단지 구성에 따라 출렁이니 방향만 참고하세요.</p>")
    h.append("<h3 style='margin:18px 0 4px'>신고가 대비 낮게 거래된 단지 (8~9억 · 2015년 이후)</h3>")
    if drop_rows:
        h.append("<table style='border-collapse:collapse;font-size:13px'><tr>" + "".join(
            f"<th style='{th}color:#6e6e73'>{c}</th>" for c in ["단지", "동", "최근 거래가", "신고가", "신고가 대비", "거래일"]) + "</tr>")
        for r in drop_rows:
            h.append(f"<tr><td style='{td}text-align:left'>{html.escape(r['apt'])}</td><td style='{td}'>{html.escape(r['dong'])}</td>"
                     f"<td style='{td}'>{r['price']/10000:.2f}억</td><td style='{td}'>{r['high']/10000:.2f}억 ({str(r['high_date'])[:7]})</td>"
                     f"<td style='{td}color:#1a7f37;font-weight:600'>-{r['drop_pct']}%</td><td style='{td}'>{r['deal_date']}</td></tr>")
        h.append("</table>")
        if since and since > "201906":
            h.append(f"<p style='color:#d97706;font-size:12px'>거래 이력이 {since[:4]}-{since[4:]}부터만 쌓여 있어 신고가가 실제보다 낮을 수 있습니다. 매일 과거로 채우는 중입니다.</p>")
    else:
        h.append("<p style='color:#6e6e73'>조건에 맞는 단지가 없거나 조회에 실패했습니다.</p>")
    h.append(f"<p><a href='{SITE}'>대시보드 열기</a></p></div>")
    text = f"경기 남부 84㎡ 주간 요약 ({now:%Y-%m-%d})\n{rate_line(data.get('rates'))}\n" + "\n".join(
        f"{r['name']}: {r['sale']}억 전월 {fmt(r['mom'])} 3개월 {fmt(r['m3'])} 거래 {r['trades']} → {r['signal']}" for r in rows) + f"\n{SITE}"
    return "".join(h), text


def main():
    now = datetime.now(KST)
    event, msg = os.environ.get("EVENT_NAME", ""), os.environ.get("COMMIT_MSG", "")
    if not (event == "workflow_dispatch" or "[mail]" in msg or (event == "schedule" and now.weekday() == 0)):
        print("오늘은 메일을 보내지 않습니다.")
        return 0
    user, pw, to = (os.environ.get(k, "") for k in ("MAIL_USER", "MAIL_APP_PASSWORD", "MAIL_TO"))
    if not (user and pw and to):
        print("MAIL_USER / MAIL_APP_PASSWORD / MAIL_TO 가 없어 건너뜁니다.")
        return 0
    rcpt = [x for x in to.replace(";", ",").replace(" ", ",").split(",") if x]
    data = json.loads((Path(__file__).parent / "data.json").read_text(encoding="utf-8"))
    body, text = build_mail(data, now)
    m = MIMEMultipart("alternative")
    m["Subject"], m["From"], m["To"] = f"[부동산] 경기 남부 84㎡ 주간 요약 {now:%Y-%m-%d}", user, ", ".join(rcpt)
    m.attach(MIMEText(text, "plain", "utf-8"))
    m.attach(MIMEText(body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, pw)
        refused = s.sendmail(user, rcpt, m.as_string())
    for addr, err in (refused or {}).items():
        print(f"수신 거부됨: {addr[:3]}*** -> {err}", file=sys.stderr)
    print(f"메일 발송 요청: {len(rcpt)}명 중 서버 거부 {len(refused or {})}명")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
