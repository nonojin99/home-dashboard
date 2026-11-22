# 경기 남부 84㎡ 매수 타이밍 대시보드

국토교통부 아파트 실거래가(공공데이터포털)를 매일 받아 동탄·수원 84㎡ 시세를 정리한 정적 페이지입니다.

- 매일 KST 07:17에 GitHub Actions가 `build.py`를 실행해 `site/index.html`을 만들고 GitHub Pages에 올립니다.
- 지역·권역 정의는 `build.py`의 `AREAS`, 화면은 `template.html`에 있습니다.

## 설정

1. Settings → Secrets and variables → Actions에 `DATA_GO_KR_API_KEY`(공공데이터포털 인증키, Decoding 키)를 등록합니다.
2. Settings → Pages → Build and deployment의 Source를 **GitHub Actions**로 둡니다.
3. Actions 탭에서 "대시보드 갱신"을 Run workflow로 한 번 실행합니다.

## 로컬 실행

```
DATA_GO_KR_API_KEY=... python build.py
```

## 관심 매물 추적

페이지의 **관심 매물** 탭에서 직접 확인한 매물의 호가·링크·메모를 기록할 수 있습니다. 가격 수정 시 이력이 남고, 현재 실거래 DB의 최근가·6개월 중위가·신고가와 비교해 매수점수를 보여줍니다. 브라우저 로컬 저장 방식이라 서버 설정 없이 작동하며 JSON 백업/복원을 지원합니다. 정식 매물 API 연동 규격은 `LISTINGS.md`를 참고하세요.

## Supabase 관심 매물 동기화 + 저평가 알림

1. 스키마는 이미 적용돼 있습니다(2026-11-22). 새로 설치할 때는 `SETUP_2_3.md`를 따릅니다.
2. PIN 변경은 `supabase_listings.sql` 맨 아래 INSERT 문만 새 값으로 실행합니다(`extensions.crypt` 사용).
3. 대시보드의 **관심 매물 → Supabase 개인 PIN**에 같은 값을 입력하고 `서버 동기화`를 누릅니다.
   - 서버가 비어 있고 브라우저에 기존 로컬 매물이 있으면 한 번에 서버로 이관할 수 있습니다.
   - 이후 PC/휴대폰이 같은 Supabase 목록과 가격 이력을 사용합니다.
4. GitHub → Settings → Secrets and variables → Actions에 `BUYER_LISTINGS_PIN`을 같은 값으로 추가합니다.
5. Actions의 `대시보드 갱신`을 수동 실행합니다. 기존 메일 Secrets가 설정돼 있으면 `listing_alerts.py`가 다음을 새로 감지했을 때 메일을 보냅니다.
   - 매수점수 65점 이상(매수 검토/강한 후보)
   - 최초 등록가 대비 2% 이상 가격 인하

### 보안 메모

Supabase anon 키는 브라우저용 공개 키입니다. 관심 매물 테이블은 anon/authenticated 역할의 직접 SELECT/INSERT/UPDATE/DELETE 권한을 제거하고, PIN을 검증하는 `SECURITY DEFINER` RPC만 실행하도록 구성했습니다. PIN은 GitHub 저장소 코드에 넣지 말고 Actions Secret에만 보관하세요.

### 매수점수

점수는 호가를 최근 실거래가, 최근 6개월 중위가(기대가), 신고가와 비교하는 가격 중심 휴리스틱입니다. 80점 이상은 `강한 후보`, 65~79점은 `매수 검토`, 50~64점은 `관찰`, 그 미만은 `비쌈`으로 표시합니다. 동·층·향·수리 상태·권리관계·대출조건은 별도 확인이 필요합니다.
