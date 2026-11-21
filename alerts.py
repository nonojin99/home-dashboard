"""새로 신고된 싼 거래 알림. 이미 알린 거래는 alerts_seen.json(Actions 캐시)에 기억해 중복 발송하지 않는다.

조건: 84㎡ · 8~9억 · 2015년 이후 · 최근 1개월 거래가 단지 신고가보다 10% 이상 낮음 (summary.fresh).
새 거래가 있을 때만 메일을 보낸다.
"""
import html
import json
import sys
from datetime import datetime
from pathlib import Path

import mail

SEEN = Path("alerts_seen.json")


def key(r):
    return f"{r['apt']}|{r['dong']}|{r['deal_date']}|{r['price']}"


def main():
    data = json.loads((Path(__file__).parent / "data.json").read_text(encoding="utf-8"))
    fresh = data.get("summary", {}).get("fresh") or []
    seen = set(json.loads(SEEN.read_text(encoding="utf-8"))) if SEEN.exists() else set()
    new = [r for r in fresh if key(r) not in seen]
    if not new:
        print(f"알림: 새 거래 없음 (조건 충족 {len(fresh)}건은 이미 알림)")
        return 0
    now = datetime.now(mail.KST)
    th = "padding:6px 8px;border-bottom:1px solid #ddd;text-align:right;white-space:nowrap;"
    rows = "".join(
        f"<tr><td style='{th}text-align:left'>{html.escape(r['apt'])}</td><td style='{th}'>{html.escape(r['dong'])}</td>"
        f"<td style='{th}'>{r['price']/10000:.2f}억</td><td style='{th}'>{r['high']/10000:.2f}억</td>"
        f"<td style='{th}color:#1a7f37;font-weight:600'>-{r['drop_pct']}%</td><td style='{th}'>{r['deal_date']}</td></tr>" for r in new)
    body = (f"<div style='font:14px/1.6 -apple-system,\"Apple SD Gothic Neo\",sans-serif'><h3 style='margin:0 0 6px'>신고가보다 10% 이상 싸게 거래된 단지 {len(new)}건</h3>"
            f"<table style='border-collapse:collapse;font-size:13px'><tr><th style='{th}text-align:left'>단지</th><th style='{th}'>동</th><th style='{th}'>거래가</th><th style='{th}'>신고가</th><th style='{th}'>대비</th><th style='{th}'>거래일</th></tr>{rows}</table>"
            f"<p style='color:#6e6e73;font-size:12px'>84㎡ · 8~9억 · 2015년 이후. 실거래 신고 기준이며 현재 매물이 아닙니다. 거래 이력이 아직 쌓이는 중이면 신고가가 낮게 잡혔을 수 있습니다.</p>"
            f"<p><a href='{mail.SITE}'>대시보드 열기</a></p></div>")
    text = "\n".join(f"{r['apt']} {r['dong']} {r['price']/10000:.2f}억 (신고가 대비 -{r['drop_pct']}%, {r['deal_date']})" for r in new)
    rc = mail.send(f"[부동산 알림] 신고가보다 싼 거래 {len(new)}건 {now:%m-%d}", text, body)
    if rc == 0:  # 발송이 확인된 뒤에만 '알림 완료'로 기록
        SEEN.write_text(json.dumps(sorted(seen | {key(r) for r in new}), ensure_ascii=False), encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
