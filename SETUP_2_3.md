# ② Supabase 동기화 + ③ 저평가 랭킹/알림 설정

## A. Supabase DB 설치 (최초 1회)

스키마·함수는 2026-11-22에 이미 적용했습니다. **PIN 설정만** 하면 됩니다.

1. Supabase 프로젝트 → **SQL Editor**로 이동합니다.
2. `supabase_listings.sql` 맨 아래 INSERT 문만 붙여 넣고 `CHANGE_THIS_PIN_NOW`를 본인만 아는 긴 PIN/암호로 바꿉니다.
3. **Run**을 누릅니다.

예시(실제 값은 더 길고 추측하기 어렵게):

```sql
values (1, extensions.crypt('여기에_본인_PIN', extensions.gen_salt('bf')), now())
```

PIN은 저장소 코드에 커밋하지 마세요. 위 예시는 반드시 실행 전에 직접 수정합니다.

## B. 기존 로컬 매물 → Supabase 이동

1. 배포된 대시보드 → **관심 매물** 탭
2. `Supabase 개인 PIN`에 A에서 정한 값을 입력
3. `서버 동기화` 클릭
4. 서버가 비어 있고 기존 로컬 매물이 있으면 이관 여부가 뜹니다. **확인**을 누르면 가격 이력까지 순서대로 업로드합니다.

이후 PC/휴대폰에서 같은 PIN으로 연결하면 동일 목록을 사용합니다.

## C. GitHub Actions 알림

GitHub 저장소 → **Settings → Secrets and variables → Actions → New repository secret**

- Name: `BUYER_LISTINGS_PIN`
- Secret: A에서 정한 PIN과 동일한 값

기존 메일 알림을 사용 중이면 `MAIL_USER`, `MAIL_APP_PASSWORD`, `MAIL_TO`는 그대로 재사용합니다.

Actions → **대시보드 갱신 → Run workflow**로 한 번 수동 실행해 확인합니다.

## D. 현재 알림/랭킹 기준

- 80점 이상: **강한 후보**
- 65~79점: **매수 검토**
- 50~64점: **관찰**
- 49점 이하: **비쌈**
- 또는 최초 등록가 대비 **2% 이상 인하** 시 알림 후보

점수는 호가와 `최근 실거래`, `최근 6개월 중위가(기대가)`, `신고가`의 차이를 조합합니다. 같은 매물·가격·등급은 캐시를 사용해 반복 메일을 보내지 않습니다.

## E. 다음 확장 포인트

정식 제휴 매물 API가 생기면 `source + source_id`를 넘겨 자동 수집하면 됩니다. 같은 가격으로 반복 관측되면 이력은 중복 저장되지 않고, 가격이 바뀐 경우에만 `buyer_listing_prices`에 새 행이 추가됩니다.

건축물대장 데이터가 준비되면 주차대수, 세대수, 사용승인일, 용적률/건폐율 같은 항목을 점수에 추가할 수 있습니다.
