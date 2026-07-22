⁹# apteye
A proptech hyper-engine that parses raw real estate text into a structured database for automated market analysis, gap tracking, and smart briefings.

# 🏙️ 프롭테크 하이퍼 엔진 V28 Pro (Proptech Hyper Engine)

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B.svg?style=flat&logo=Streamlit&logoColor=white)
![Pandas](https://img.shields.io/badge/Pandas-150458.svg?style=flat&logo=pandas&logoColor=white)
![Plotly](https://img.shields.io/badge/Plotly-3F4F75.svg?style=flat&logo=plotly&logoColor=white)

복잡한 부동산 원문 텍스트(매매/전월세 호가, 실거래가)를 단 1초 만에 정밀 데이터베이스로 변환하고 시각화하는 **현장 밀착형 부동산 시세 분석 및 브리핑 자동화 대시보드**입니다.

---

## 🚀 주요 기능 (Key Features)

* **유니버설 원문 파서 (Universal Text Parser)**: 네이버페이 부동산, 아파트실거래, 국토부 등 파편화된 원문 텍스트에서 정규표현식(Regex)을 통해 데이터를 추출하고 표준화합니다.
* **층수 통제 정밀 갭(Gap) 트래킹**: 저/중/고/탑층 그룹핑을 통해 매매-전세 간 실제 체결 가능한 '실투자 갭(Gap)'을 연산합니다.
* **층수 보정 괴리율 (Floor-Adjusted Spread)**: 실거래 평균가와 호가의 차이를 단순 계산하지 않고 층수 프리미엄을 반영하여 진짜 급매를 판별합니다.
* **스마트 브리핑 엑스포트**: 수집된 데이터를 카카오톡 모바일 가독성에 맞춘 4원화 포맷(동별, 타입별, 방향별, 요약)으로 클립보드에 자동 생성합니다.
* **데이터 시각화**: Plotly 기반의 시계열 호가 밴드, 층x타입 호가 히트맵, 개별 매물 호가 인하 워터폴(Waterfall) 차트를 제공합니다.

---

## 🏗️ 시스템 아키텍처 및 데이터 파이프라인

본 엔진은 텍스트 파싱부터 시각화까지 **4단계 파이프라인**을 거쳐 동작합니다.

1. **Input (Raw Text)**: 사용자가 UI를 통해 매매/전월세/실거래 텍스트 원문(Unstructured Data) 입력.
2. **Parsing & ETL Layer**: 정규표현식(Regex) 모듈이 가동되어 가격(문자열 $\rightarrow$ 실수 억 단위), 전용면적(소수점 매핑), 층수, 동 번호를 구조화된 `Pandas DataFrame`으로 변환.
3. **Storage (Local CSV DB)**: 새로 파싱된 데이터를 기존 `_db.csv` 파일들과 병합. `drop_duplicates()`를 통해 고유 식별자(수집일, 동, 타입, 층 등) 기준 중복 데이터(Noise) 제거 후 스냅샷 저장.
4. **Analytics & Visualization Layer**: 병합된 DB를 메모리에 로드하여 그룹핑 연산(괴리율, 갭, DOM)을 수행하고 Streamlit 레이아웃과 Plotly 차트에 실시간 렌더링.

---

## 🔬 핵심 알고리즘 및 연산식 (Core Algorithms)

### 1. 층수 범주화 (Floor Categorization Algorithm)
모든 매물과 실거래는 단순 층수가 아닌 **계급 구간(Tier)**으로 변환되어 통제됩니다.
* **로직**: `총 층수(Total Floors)` 대비 `현재 층(Current Floor)`의 비율(Ratio) 계산.
  * $ Ratio \le 0.3 $ : `저층`
  * $ 0.3 < Ratio \le 0.7 $ : `중층`
  * $ Ratio > 0.7 $ : `고층`
  * 최상층 : `탑층`

### 2. 층수 보정 괴리율 (Floor-Adjusted Disparity Index)
전체 평형의 평균 실거래가로 호가를 평가하는 통계적 오류(Simpson's paradox)를 방지하기 위해 층수 통제 변수를 도입했습니다.

$$ \text{괴리율(\%)} = \left( \frac{\text{현재 최저 호가} - \text{동일 층수 그룹 최근 실거래 평균가}}{\text{동일 층수 그룹 최근 실거래 평균가}} \right) \times 100 $$

*(단, 동일 층수 그룹의 실거래 데이터가 없을 경우 해당 타입 전체 평균으로 Fallback 렌더링)*

### 3. 실전 체결 갭 (Practical Investment Gap)
갭 투자 시세 왜곡(예: 저층 매매가 - 최고층 전세가 = 가상 갭)을 방지하는 정밀 매칭 연산입니다.

$$ \text{실투자 갭} = \min(\text{매매가}_{i}) - \max(\text{전세가}_{i}) $$
*(where $i$ is the identical Floor Category Group)*

### 4. 2원화 타입 파싱 전략 (Dual-Type Normalization)
* **UI 표시용 (Listing Type)**: `84.98A`, `84.93E` 등 소수점 둘째 자리와 영문 알파벳을 100% 보존. (판상형 vs 타워형 구조 프리미엄 구분 용도)
* **데이터 매칭용 (Group Type)**: `84`, `114` 등 소수점 이하를 버린 정수형 타입. (과거 국토부 실거래가 및 전세 데이터와 Join/Merge 연산을 수행하기 위한 Foreign Key 역할)

---

## 🗄️ 데이터베이스 스키마 (Database Schema)

로컬에 자동 생성되는 CSV DB의 핵심 컬럼 구조입니다.

| DB 파일명 | 기본 Key (중복제거 기준) | 핵심 포함 데이터 컬럼 |
| :--- | :--- | :--- |
| `listings_db.csv` | 단지명, 수집일, 동, 타입, 층 | 매물등록일, 금액_하한(억), 방향, 중개사수, 중개사명 |
| `rentals_db.csv` | 단지명, 수집일, 동, 타입, 거래구분, 층 | 매물등록일, 보증금(억), 방향, 중개사수 |
| `transactions_db.csv` | 단지명, 날짜, 동, 타입, 층 | 금액_하한(억), 거래유형(직/중개) |
| `raw_inputs_db.csv` | 단지명, 날짜, 유형(매매/전세/실거래) | 원문 텍스트 (History 백업용) |

---

## 🛠️ 기술 스택 (Tech Stack)

* **Language**: Python 3.9+
* **Web Framework**: Streamlit 1.30+
* **Data Processing**: Pandas, NumPy, Regex(`re`)
* **Data Visualization**: Plotly (Express & Graph Objects)

---

## ⚙️ 설치 및 실행 방법 (Installation & Usage)

### 1. 레포지토리 클론
```bash
git clone [https://github.com/본인계정명/proptech-hyper-engine.git](https://github.com/본인계정명/proptech-hyper-engine.git)
cd proptech-hyper-engine
