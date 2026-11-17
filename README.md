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
