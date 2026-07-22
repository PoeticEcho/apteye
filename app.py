import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import re
import os
from datetime import datetime
import numpy as np

# --- 페이지 기본 설정 ---
st.set_page_config(page_title="프롭테크 하이퍼 엔진 V28.15 Pro", layout="wide", initial_sidebar_state="expanded")

# --- UI 스타일링 ---
st.markdown("""
    <style>
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
    h1, h2, h3 { color: #0F172A; font-weight: 800; } 
    .stAlert { border-radius: 10px; }
    code { font-family: 'Pretendard', sans-serif !important; font-size: 0.95rem !important; }
    div[data-testid="stMetricValue"] { font-size: 1.8rem; font-weight: 700; color: #1E293B; }
    </style>
""", unsafe_allow_html=True)

# --- 0. 로컬 DB 파일 경로 설정 ---
TX_DB_PATH = 'transactions_db.csv'       
LISTING_DB_PATH = 'listings_db.csv'      
RENTAL_DB_PATH = 'rentals_db.csv'       
RAW_DB_PATH = 'raw_inputs_db.csv'         

# --- Session State 초기화 ---
if 'raw_text_1' not in st.session_state:
    st.session_state['raw_text_1'] = ""
if 'raw_text_2' not in st.session_state:
    st.session_state['raw_text_2'] = ""

# --- 1. 유니버설 파서 엔진 ---

def convert_price_single(p_str):
    """
    ✨ [V28.15] 실거래가/호가 한글 원문 정밀 가격 파서
    - '11억3' -> 11.0003 (11억 3만원)
    - '11억3천3' -> 11.3003 (11억 3003만원)
    - '11억303' -> 11.0303 (11억 303만원)
    - '15억7천238' -> 15.7238 (15억 7238만원)
    """
    if not p_str or pd.isna(p_str): return 0.0
    p_str = str(p_str).replace('(고)', '').replace(' ', '').replace(',', '').replace('\n', '').strip()
    
    uk = 0
    man = 0
    
    if '억' in p_str:
        parts = p_str.split('억')
        uk_part = parts[0].strip()
        uk = int(uk_part) if uk_part.isdigit() else 0
        rest = parts[1].strip() if len(parts) > 1 else ""
    else:
        rest = p_str
        
    if '천' in rest:
        c_parts = rest.split('천')
        cheon_val = int(c_parts[0]) if c_parts[0].isdigit() else 0
        tail_val = int(c_parts[1]) if (len(c_parts) > 1 and c_parts[1].isdigit()) else 0
        man = (cheon_val * 1000) + tail_val
    elif rest.isdigit():
        man = int(rest)
            
    return uk + (man / 10000.0)

def process_price_columns(df):
    """가격 컬럼 생성 및 전처리"""
    if df.empty or '금액_문자열' not in df.columns: return df
    def extract_ranges(val):
        val_str = str(val).replace('\n', '').strip()
        if '~' in val_str:
            parts = val_str.split('~')
            low = convert_price_single(parts[0])
            high = convert_price_single(parts[1])
            return pd.Series([low, high, (low+high)/2])
        else:
            p = convert_price_single(val_str)
            return pd.Series([p, p, p])
    df[['금액_하한(억)', '금액_상한(억)', '금액_평균(억)']] = df['금액_문자열'].apply(extract_ranges)
    df['금액(억)'] = df['금액_하한(억)'] 
    return df

def categorize_floor(floor_str):
    """층수 정규화 (저층/중층/고층/탑층 범주화)"""
    f_str = str(floor_str).strip()
    if '탑' in f_str: return '탑층'
    elif '고' in f_str: return '고층'
    elif '중' in f_str: return '중층'
    elif '저' in f_str: return '저층'
    
    nums = re.findall(r'\d+', f_str)
    if nums:
        f_num = int(nums[0])
        total_f = int(nums[1]) if len(nums) > 1 else 35
        if f_num == total_f and total_f > 10: return '탑층'
        ratio = f_num / total_f
        if ratio <= 0.3: return '저층'
        elif ratio <= 0.7: return '중층'
        else: return '고층'
    return '중층'

def is_valid_date(y, m, d):
    """날짜 유효성 검증"""
    try:
        m, d = int(m), int(d)
        return 1 <= m <= 12 and 1 <= d <= 31
    except:
        return False

def parse_transactions(text):
    """실거래가 파서"""
    if not text.strip(): return pd.DataFrame()
    parsed = []
    
    lines = text.split('\n')
    curr_y = "2026"
    for line in lines:
        line_s = line.strip()
        y_match = re.search(r'(202[0-9])년', line_s)
        if y_match:
            curr_y = y_match.group(1)

        if "계약취소" in line_s or "해지" in line_s:
            continue

        tbl_m = re.search(r'(\d{1,2})월\s*(\d{1,2})일\s+.*?\b(\d+)층\s+.*?(?:직거래|최고|최저)?\s*([0-9]+억[0-9천\s,\.]*)', line_s)
        if tbl_m:
            mm_str, dd_str = tbl_m.group(1), tbl_m.group(2)
            if is_valid_date(curr_y, mm_str, dd_str):
                parsed.append({
                    '날짜': f"{curr_y}.{mm_str.zfill(2)}.{dd_str.zfill(2)}",
                    '타입': "전체", 
                    '금액_문자열': tbl_m.group(4),
                    '층': tbl_m.group(3),
                    '동': "동미상",
                    '거래유형': "직거래" if "직거래" in line_s else "중개거래",
                    '데이터구분': '실거래'
                })

    b_blocks = re.split(r'(?=(?:[가-힣]+구|[가-힣]+동)\n|\n[가-힣0-9]+분양권|\n[가-힣0-9]+아파트)', text)
    for b_block in b_blocks:
        contract_matches = re.finditer(r'([0-9]+억[0-9천\s,\.]*|[\d,]+만)[\s\S]{1,150}?([\d\.]+)㎡\s*(\d+[A-Z]?평)?[\s\S]{1,50}?(\d+)층(?:\s*(?<!\d)(\d{1,4})동?)?[\s\S]{1,30}?(중개거래|직거래)?[\s\S]{1,30}?((?:20)?2[0-9]\.\d{2}\.\d{2})\s*계약', b_block)
        for cm in contract_matches:
            p_str = cm.group(1).strip()
            area_val = float(cm.group(2))
            pyeong_str = cm.group(3) if cm.group(3) else ""
            floor_str = cm.group(4)
            dong_str = f"{cm.group(5)}동" if cm.group(5) else "동미상"
            deal_type = cm.group(6) if cm.group(6) else "중개거래"
            date_raw = cm.group(7)

            if date_raw.startswith('26.') or date_raw.startswith('25.') or date_raw.startswith('24.'):
                full_date = f"20{date_raw}"
            else:
                full_date = date_raw

            date_parts = full_date.split('.')
            if len(date_parts) == 3 and is_valid_date(date_parts[0], date_parts[1], date_parts[2]):
                if 10.0 <= area_val <= 300.0:
                    area_base = f"{area_val:.2f}".rstrip('0').rstrip('.') if '.' in str(area_val) else str(int(area_val))
                    alpha_m = re.search(r'([A-Z])평', pyeong_str)
                    if alpha_m and not area_base.endswith(alpha_m.group(1)):
                        area_base += alpha_m.group(1)

                    parsed.append({
                        '날짜': full_date, 
                        '타입': area_base, 
                        '금액_문자열': p_str,
                        '층': floor_str, 
                        '동': dong_str,
                        '거래유형': deal_type, 
                        '데이터구분': '실거래'
                    })

    current_year = "2026"
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        y_m = re.search(r'(202[0-9])년', line)
        if y_m:
            current_year = y_m.group(1)
            
        date_m = re.match(r'^((?:20)?2[0-9])?\.?(\d{2})\.(\d{2})', line)
        if date_m:
            yr = date_m.group(1) if date_m.group(1) else current_year
            if len(yr) == 2: yr = f"20{yr}"
            mm, dd = date_m.group(2), date_m.group(3)
            
            if is_valid_date(yr, mm, dd):
                full_date = f"{yr}.{mm}.{dd}"
                block_lines = [line]
                j = i + 1
                while j < len(lines):
                    next_line = lines[j].strip()
                    if re.search(r'(202[0-9])년', next_line) or re.match(r'^((?:20)?2[0-9])?\.?(\d{2})\.(\d{2})', next_line):
                        break
                    block_lines.append(next_line)
                    j += 1
                
                block_txt = "\n".join(block_lines)
                i = j
                
                if "해지" in block_txt or "계약취소" in block_txt:
                    continue
                    
                price_m = re.search(r'([0-9]+억[0-9천\s,\.]*|[\d,]+만)(?:\(고\))?', block_txt)
                type_m = re.search(r'([\d\.]+)[㎡]*\s*(\d+[A-Z]?평)?', block_txt)
                floor_m = re.search(r'(\d+)층(?:\s+(?<!\d)(\d{1,4})\s*동?)?', block_txt)
                deal_type_m = re.search(r'(직거래|중개거래)', block_txt)
                dong_bottom_m = re.search(r'\n(?<!\d)(\d{1,4})\s*$', block_txt.strip())
                
                if price_m and type_m:
                    area_val = float(type_m.group(1))
                    if 10.0 <= area_val <= 300.0:
                        area_base = f"{area_val:.2f}".rstrip('0').rstrip('.') if '.' in str(area_val) else str(int(area_val))
                        pyeong_str = type_m.group(2) if type_m.group(2) else ""
                        alpha_m = re.search(r'([A-Z])평', pyeong_str)
                        if alpha_m and not area_base.endswith(alpha_m.group(1)):
                            area_base += alpha_m.group(1)
                            
                        dong_str = f"{floor_m.group(2)}동" if floor_m and floor_m.group(2) else ("동미상" if not dong_bottom_m else f"{dong_bottom_m.group(1)}동")
                        
                        parsed.append({
                            '날짜': full_date, 
                            '타입': area_base, 
                            '금액_문자열': price_m.group(1).strip(),
                            '층': floor_m.group(1) if floor_m else "0", 
                            '동': dong_str,
                            '거래유형': deal_type_m.group(1) if deal_type_m else "중개거래", 
                            '데이터구분': '실거래'
                        })
            else:
                i += 1
        else:
            i += 1

    df_res = pd.DataFrame(parsed)
    if df_res.empty: return df_res
    
    df_res = process_price_columns(df_res)
    subset_cols = [c for c in ['날짜', '층', '금액_하한(억)'] if c in df_res.columns]
    return df_res.drop_duplicates(subset=subset_cols, keep='first')

def parse_naver_listings(text):
    """네이버 매매 매물 파서"""
    if not text.strip(): return pd.DataFrame()
    today_str = datetime.now().strftime("%Y-%m-%d")
    parsed = []
    
    blocks = re.split(r'(?=(?:[가-힣0-9()]+\s+)?(?<!\d)\d+동\s*\n?\s*매매)', text)
    for block in blocks:
        if "매매" not in block: continue
        
        dong_m = re.search(r'(?<!\d)(\d+동)', block)
        price_m = re.search(r'매매[^\S\r\n]+([0-9억,~\-\s]+)', block)
        type_m = re.search(r'전용\s*([\d\.]+[A-Z]*)', block)
        
        floor_dir_m = re.search(r'([가-힣0-9]+)/\d+층\s*\n?\s*((?:남서|남동|북서|북동|남|북|동|서)향)?', block)
        if not floor_dir_m:
            floor_dir_m = re.search(r'([가-힣0-9]+)/\d+층((?:남서|남동|북서|북동|남|북|동|서)향)?', block)

        date_m = re.search(r'(202[3-6]\.\d{2}\.\d{2})', block)
        broker_cnt_m = re.search(r'중개사\s*(\d+)곳', block)
        
        broker_name = ""
        broker_name_m = re.search(r'(?:확인매물|등록)\s+\d{4}\.\d{2}\.\d{2}\s*\n(?P<name>.*?)\n', block)
        if broker_name_m: broker_name = broker_name_m.group('name').strip()

        if price_m and date_m and dong_m:
            parsed.append({
                '수집일': today_str, 
                '매물등록일': date_m.group(1), 
                '동': dong_m.group(1),
                '타입': type_m.group(1) if type_m else "미상",
                '금액_문자열': price_m.group(1).strip(),
                '층': floor_dir_m.group(1) if floor_dir_m else "미상",
                '방향': floor_dir_m.group(2) if floor_dir_m and floor_dir_m.group(2) else "방향미상",
                '중개사수': int(broker_cnt_m.group(1)) if broker_cnt_m else 1,
                '중개사명': broker_name, 
                '데이터구분': '네이버매물(매매)'
            })
    return pd.DataFrame(parsed)

def parse_naver_rentals(text):
    """네이버 전월세 매물 파서"""
    if not text.strip(): return pd.DataFrame()
    today_str = datetime.now().strftime("%Y-%m-%d")
    parsed = []
    
    blocks = re.split(r'(?=(?:[가-힣0-9()]+\s+)?(?<!\d)\d+동\s*\n?\s*(?:전세|월세))', text)
    for block in blocks:
        if "전세" not in block and "월세" not in block: continue
        
        dong_m = re.search(r'(?<!\d)(\d+동)', block)
        deal_kind = "전세" if re.search(r'(?<!\d)\d+동\s*\n?\s*전세', block) else "월세"
        
        price_m = re.search(r'(?:전세|월세)[^\S\r\n]+([0-9억,~\-\s/]+)', block)
        type_m = re.search(r'전용\s*([\d\.]+[A-Z]*)', block)
        
        floor_dir_m = re.search(r'([가-힣0-9]+)/\d+층\s*\n?\s*((?:남서|남동|북서|북동|남|북|동|서)향)?', block)
        if not floor_dir_m:
            floor_dir_m = re.search(r'([가-힣0-9]+)/\d+층((?:남서|남동|북서|북동|남|북|동|서)향)?', block)

        date_m = re.search(r'(202[3-6]\.\d{2}\.\d{2})', block)
        broker_cnt_m = re.search(r'중개사\s*(\d+)곳', block)

        if price_m and date_m and dong_m:
            p_raw = price_m.group(1).strip()
            deposit = 0.0
            
            p_target = p_raw.split('~')[0].strip() if '~' in p_raw else p_raw
            
            if deal_kind == "전세":
                deposit = convert_price_single(p_target)
            else:
                dep_part = p_target.split('/')[0] if '/' in p_target else p_target
                deposit = convert_price_single(dep_part)

            parsed.append({
                '수집일': today_str, 
                '매물등록일': date_m.group(1), 
                '동': dong_m.group(1),
                '거래구분': deal_kind,
                '타입': type_m.group(1) if type_m else "미상",
                '금액_문자열': p_raw,
                '보증금(억)': deposit,
                '층': floor_dir_m.group(1) if floor_dir_m else "미상",
                '방향': floor_dir_m.group(2) if floor_dir_m and floor_dir_m.group(2) else "방향미상",
                '중개사수': int(broker_cnt_m.group(1)) if broker_cnt_m else 1,
                '데이터구분': '네이버매물(전월세)'
            })
    return pd.DataFrame(parsed)

def smart_auto_parse_and_update(raw_text, complex_name):
    """
    ✨ [V28.15 핵심 파서 & HUD 상태 반환]
    저장 후 카운트 메트릭 반환
    """
    if not raw_text.strip(): return 0, 0, 0
    
    today = datetime.now().strftime("%Y-%m-%d")
    tx_cnt, ls_cnt, rn_cnt = 0, 0, 0
    
    # 1. 실거래가 파싱
    df_tx = parse_transactions(raw_text)
    if not df_tx.empty:
        tx_cnt = len(df_tx)
        update_db(df_tx.assign(단지명=complex_name), TX_DB_PATH, ['단지명', '날짜', '층', '금액_하한(억)'])
        update_db(pd.DataFrame([{'날짜': today, '단지명': complex_name, '유형': '실거래', '원문': raw_text}]), RAW_DB_PATH, ['날짜', '단지명', '유형'])
        
    # 2. 네이버 매매 파싱
    df_ls = parse_naver_listings(raw_text)
    if not df_ls.empty:
        ls_cnt = len(df_ls)
        update_db(df_ls.assign(단지명=complex_name), LISTING_DB_PATH, ['단지명', '수집일', '동', '타입', '금액_하한(억)', '층'])
        update_db(pd.DataFrame([{'날짜': today, '단지명': complex_name, '유형': '매매호가', '원문': raw_text}]), RAW_DB_PATH, ['날짜', '단지명', '유형'])

    # 3. 네이버 전월세 파싱
    df_rn = parse_naver_rentals(raw_text)
    if not df_rn.empty:
        rn_cnt = len(df_rn)
        update_db(df_rn.assign(단지명=complex_name), RENTAL_DB_PATH, ['단지명', '수집일', '동', '타입', '거래구분', '보증금(억)', '층'])
        update_db(pd.DataFrame([{'날짜': today, '단지명': complex_name, '유형': '전월세', '원문': raw_text}]), RAW_DB_PATH, ['날짜', '단지명', '유형'])

    return tx_cnt, ls_cnt, rn_cnt

def update_db(new_df, db_path, subset_keys):
    if new_df.empty: return True
    if '금액_문자열' in new_df.columns and '보증금(억)' not in new_df.columns: 
        new_df = process_price_columns(new_df)
    try:
        if os.path.exists(db_path):
            existing_df = pd.read_csv(db_path)
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            valid_subset = [k for k in subset_keys if k in combined_df.columns]
            combined_df = combined_df.drop_duplicates(subset=valid_subset, keep='last')
        else: combined_df = new_df
        combined_df.to_csv(db_path, index=False, encoding='utf-8-sig')
        return True
    except: return False

def delete_complex_data(complex_name):
    paths_keys = [
        (LISTING_DB_PATH, '단지명'),
        (RENTAL_DB_PATH, '단지명'),
        (TX_DB_PATH, '단지명'),
        (RAW_DB_PATH, '단지명')
    ]
    for path, col in paths_keys:
        if os.path.exists(path):
            df = pd.read_csv(path)
            if col in df.columns:
                filtered_df = df[df[col] != complex_name]
                filtered_df.to_csv(path, index=False, encoding='utf-8-sig')

def rollback_last_snapshot():
    for path in [LISTING_DB_PATH, RENTAL_DB_PATH, TX_DB_PATH, RAW_DB_PATH]:
        if os.path.exists(path):
            df = pd.read_csv(path)
            date_col = '수집일' if '수집일' in df.columns else ('날짜' if '날짜' in df.columns else None)
            if date_col and not df.empty:
                max_date = df[date_col].max()
                df_filtered = df[df[date_col] != max_date]
                df_filtered.to_csv(path, index=False, encoding='utf-8-sig')

# --- 2. 사이드바 제어 센터 ---

with st.sidebar:
    st.title("🏙️ 대시보드 제어")
    
    with st.popover("⚙️ 스마트 데이터 입력 & DB 정제", use_container_width=True):
        st.subheader("📥 개별 단지 스마트 입력 센터")
        st.caption("💡 원문을 복사해 넣고 하단 '저장' 버튼을 누르면 자동 파싱 후 입력창이 비워집니다.")
        
        # --- 단지 1 섹션 ---
        st.markdown("---")
        name1 = st.text_input("🏢 단지 1 이름", value="범어자이(주상복합)", key="n1")
        raw1 = st.text_area("단지 1 원문 입력", value=st.session_state['raw_text_1'], height=150, key="r1_input", help="매매, 전월세, 실거래가 원문 텍스트를 붙여넣으세요.")
        
        if st.button("💾 [단지 1] 데이터 파싱 및 저장", use_container_width=True):
            if raw1.strip():
                if "범어자이" in name1 and ("오페라" in raw1 or "고성동" in raw1):
                    st.warning(f"⚠️ [{name1}] 입력 칸에 다른 단지 원문이 섞여 있는 것 같습니다.")
                
                tx_c, ls_c, rn_c = smart_auto_parse_and_update(raw1, name1)
                st.session_state['raw_text_1'] = "" # 입력창 리셋
                
                total_c = tx_c + ls_c + rn_c
                if total_c > 0:
                    st.toast(f"🎉 [{name1}] {total_c}건 파싱 완료! (실거래 {tx_c}건, 매매 {ls_c}건, 전월세 {rn_c}건)", icon="✅")
                    st.success(f"✅ [{name1}] 파싱 완료: 실거래가 {tx_c}건 / 매매호가 {ls_c}건 / 전월세 {rn_c}건 적재됨")
                else:
                    st.warning(f"⚠️ [{name1}] 파싱된 매물이 없습니다. 원문 포맷을 확인하세요.")
                st.rerun()

        # --- 단지 2 섹션 ---
        st.markdown("---")
        name2 = st.text_input("🏢 단지 2 이름", value="대구역오페라더블유(주상복합)", key="n2")
        raw2 = st.text_area("단지 2 원문 입력", value=st.session_state['raw_text_2'], height=150, key="r2_input", help="매매, 전월세, 실거래가 원문 텍스트를 붙여넣으세요.")
        
        if st.button("💾 [단지 2] 데이터 파싱 및 저장", use_container_width=True):
            if raw2.strip():
                if "오페라" in name2 and ("범어" in raw2 or "수성구" in raw2):
                    st.warning(f"⚠️ [{name2}] 입력 칸에 다른 단지 원문이 섞여 있는 것 같습니다.")
                
                tx_c, ls_c, rn_c = smart_auto_parse_and_update(raw2, name2)
                st.session_state['raw_text_2'] = "" # 입력창 리셋
                
                total_c = tx_c + ls_c + rn_c
                if total_c > 0:
                    st.toast(f"🎉 [{name2}] {total_c}건 파싱 완료! (실거래 {tx_c}건, 매매 {ls_c}건, 전월세 {rn_c}건)", icon="✅")
                    st.success(f"✅ [{name2}] 파싱 완료: 실거래가 {tx_c}건 / 매매호가 {ls_c}건 / 전월세 {rn_c}건 적재됨")
                else:
                    st.warning(f"⚠️ [{name2}] 파싱된 매물이 없습니다. 원문 포맷을 확인하세요.")
                st.rerun()

        st.markdown("---")
        st.subheader("🛠️ DB 데이터 수정 & 관리")
        
        c_del1, c_del2 = st.columns(2)
        with c_del1:
            if st.button("↺ 최근 스냅샷 롤백", help="가장 최근 입력한 날짜의 데이터를 전체 DB에서 삭제합니다."):
                rollback_last_snapshot()
                st.success("최근 입력 데이터가 롤백되었습니다!")
                st.rerun()
        with c_del2:
            all_complexes = []
            for p in [LISTING_DB_PATH, RENTAL_DB_PATH, TX_DB_PATH]:
                if os.path.exists(p):
                    all_complexes.extend(list(pd.read_csv(p)['단지명'].unique()))
            unique_complexes = list(set(all_complexes))
            
            del_target = st.selectbox("삭제할 단지 선택", unique_complexes if unique_complexes else ["없음"], key="del_tgt")
            if st.button("🗑️ 선택 단지 DB 완전 삭제"):
                if del_target != "없음":
                    delete_complex_data(del_target)
                    st.success(f"[{del_target}] 의 모든 데이터가 삭제되었습니다!")
                    st.rerun()

    st.markdown("---")
    
    all_known_complexes = set()
    for path in [LISTING_DB_PATH, RENTAL_DB_PATH, TX_DB_PATH]:
        if os.path.exists(path):
            df_temp = pd.read_csv(path)
            if '단지명' in df_temp.columns:
                all_known_complexes.update(df_temp['단지명'].unique())
    
    known_list = list(all_known_complexes) if all_known_complexes else ["범어자이(주상복합)"]
    selected_complex = st.selectbox("🏢 분석 단지 선택", known_list)

    st.markdown("---")
    st.subheader("📥 DB 다운로드 센터")
    if os.path.exists(LISTING_DB_PATH):
        st.download_button("📥 매매 DB (CSV)", data=open(LISTING_DB_PATH, 'rb'), file_name="listings_db.csv", mime="text/csv", use_container_width=True)
    if os.path.exists(RENTAL_DB_PATH):
        st.download_button("📥 전월세 DB (CSV)", data=open(RENTAL_DB_PATH, 'rb'), file_name="rentals_db.csv", mime="text/csv", use_container_width=True)
    if os.path.exists(TX_DB_PATH):
        st.download_button("📥 실거래 DB (CSV)", data=open(TX_DB_PATH, 'rb'), file_name="transactions_db.csv", mime="text/csv", use_container_width=True)

# --- 3. 메인 분석 대시보드 ---

st.title(f"🏙️ {selected_complex} 정밀 라이프사이클 V28.15 Pro")

ls_df = pd.read_csv(LISTING_DB_PATH) if os.path.exists(LISTING_DB_PATH) else pd.DataFrame()
tx_df = pd.read_csv(TX_DB_PATH) if os.path.exists(TX_DB_PATH) else pd.DataFrame()
rn_df = pd.read_csv(RENTAL_DB_PATH) if os.path.exists(RENTAL_DB_PATH) else pd.DataFrame()

target_ls = ls_df[ls_df['단지명'] == selected_complex].copy() if not ls_df.empty else pd.DataFrame()
target_tx = tx_df[tx_df['단지명'] == selected_complex].copy() if not tx_df.empty else pd.DataFrame()
target_rn = rn_df[rn_df['단지명'] == selected_complex].copy() if not rn_df.empty else pd.DataFrame()

has_data = not target_ls.empty or not target_tx.empty or not target_rn.empty

if has_data:
    today_ls = pd.DataFrame()
    if not target_ls.empty:
        target_ls['수집일_dt'] = pd.to_datetime(target_ls['수집일'])
        latest_dt = target_ls['수집일_dt'].max()
        today_ls = target_ls[target_ls['수집일_dt'] == latest_dt].copy()
        
        today_ls['매물등록일_dt'] = pd.to_datetime(today_ls['매물등록일'], errors='coerce')
        today_ls['DOM(일)'] = (today_ls['수집일_dt'] - today_ls['매물등록일_dt']).dt.days.fillna(0).astype(int)
        today_ls['층_구분'] = today_ls['층'].apply(categorize_floor)
        today_ls['타입_그룹'] = today_ls['타입'].astype(str).str.extract(r'(\d+)')[0]
        
        price_cuts, first_prices = [], []
        for _, row in today_ls.iterrows():
            hist = target_ls[(target_ls['동'] == row['동']) & (target_ls['타입'] == row['타입']) & (target_ls['층'] == row['층'])]
            if len(hist) > 1:
                first_p = hist.sort_values('수집일_dt').iloc[0]['금액_하한(억)']
                diff = row['금액_하한(억)'] - first_p
                price_cuts.append(diff)
                first_prices.append(first_p)
            else:
                price_cuts.append(0.0)
                first_prices.append(row['금액_하한(억)'])
        today_ls['가격변동액(억)'] = price_cuts
        today_ls['최초호가(억)'] = first_prices

        if not target_tx.empty:
            target_tx['타입_그룹'] = target_tx['타입'].astype(str).str.extract(r'(\d+)')[0]
            target_tx['층_구분'] = target_tx['층'].apply(categorize_floor)
            
            tx_floor_summary = target_tx.groupby(['타입_그룹', '층_구분'])['금액_하한(억)'].mean().reset_index()
            tx_floor_summary.rename(columns={'금액_하한(억)': '층별실거래평균(억)'}, inplace=True)
            
            tx_type_summary = target_tx.groupby('타입_그룹')['금액_하한(억)'].mean().reset_index()
            tx_type_summary.rename(columns={'금액_하한(억)': '타입실거래평균(억)'}, inplace=True)
            
            today_ls = pd.merge(today_ls, tx_floor_summary, on=['타입_그룹', '층_구분'], how='left')
            today_ls = pd.merge(today_ls, tx_type_summary, on='타입_그룹', how='left')
            
            today_ls['최근실거래평균(억)'] = today_ls['층별실거래평균(억)'].fillna(today_ls['타입실거래평균(억)'])
            today_ls['층보정_괴리율(%)'] = ((today_ls['금액_하한(억)'] - today_ls['최근실거래평균(억)']) / today_ls['최근실거래평균(억)']) * 100
        else:
            today_ls['최근실거래평균(억)'] = np.nan
            today_ls['층보정_괴리율(%)'] = np.nan

    # HUD 메트릭
    m1, m2, m3, m4 = st.columns(4)
    min_price = today_ls['금액_하한(억)'].min() if not today_ls.empty else np.nan
    latest_tx_p = target_tx['금액_하한(억)'].iloc[-1] if not target_tx.empty else np.nan
    avg_spread = today_ls['층보정_괴리율(%)'].mean() if not today_ls.empty and '층보정_괴리율(%)' in today_ls.columns else np.nan
    long_dom_cnt = len(today_ls[today_ls['DOM(일)'] >= 60]) if not today_ls.empty else 0

    m1.metric("🏆 매매 최저 호가", f"{min_price:.2f} 억" if pd.notnull(min_price) else "N/A")
    m2.metric("📑 최근 실거래가", f"{latest_tx_p:.2f} 억" if pd.notnull(latest_tx_p) else "N/A")
    m3.metric("📉 층보정 평균 괴리율", f"{avg_spread:+.2f}%" if pd.notnull(avg_spread) else "N/A")
    m4.metric("⏳ 60일+ 미소진 매물", f"{long_dom_cnt} 건", delta="급매 협상 여지" if long_dom_cnt>0 else "매물 소진 양호")

    st.markdown("---")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "🎯 실거래 매칭", 
        "💬 카톡 브리핑 엑스포트", 
        "🔑 전월세 & 정밀 갭 트래킹", 
        "📊 괴리율 & 체류기간", 
        "📈 시각화 차트", 
        "📅 원문 히스토리"
    ])

    # --- TAB 1: 실거래 매칭 ---
    with tab1:
        st.markdown(f"### 🎯 [{selected_complex}] 최근 실거래가 및 매물 현황")
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("📑 최근 실거래가 기록")
            if not target_tx.empty:
                st.dataframe(target_tx[['날짜', '동', '타입', '층', '금액_문자열', '거래유형']].sort_values('날짜', ascending=False), use_container_width=True)
            else: st.info("실거래가 데이터가 없습니다.")
        with c2:
            st.subheader("🏡 현재 최신 매매 호가")
            if not today_ls.empty:
                st.dataframe(today_ls.sort_values('금액_하한(억)')[['매물등록일', '동', '타입', '층', '방향', '금액_문자열', '중개사수']], use_container_width=True)
            else: st.info("현재 수집된 매매 호가가 없습니다.")

    # --- TAB 2: 카톡 브리핑 엑스포트 ---
    with tab2:
        st.markdown("### 💬 모바일 카카오톡 맞춤 브리핑 리포터")
        if not today_ls.empty:
            latest_dt = today_ls['수집일_dt'].max()
            group_option = st.radio(
                "카톡 브리핑 포맷 선택", 
                ["🏢 동별 (기본 - 전체)", "📐 타입별 (전체)", "🧭 방향별 (전체)", "🚀 컴팩트 요약 (최저가 Top 5)"], 
                horizontal=True
            )

            weekday_str = ["월", "화", "수", "목", "금", "토", "일"][latest_dt.weekday()]
            katalk_briefing = f"📢 [{selected_complex} 오늘 브리핑]\n"
            katalk_briefing += f"🗓️ 기준: {latest_dt.strftime('%m/%d')} ({weekday_str})\n"
            katalk_briefing += f"🏠 총 매물: {len(today_ls)}건\n\n"

            def format_briefing_item(row, hide_dong=False, hide_type=False, hide_dir=False):
                raw_floor = str(row['층']).split('/')[0] if '/' in str(row['층']) else str(row['층'])
                floor_txt = raw_floor if str(raw_floor).endswith('층') else f"{raw_floor}층"
                price_str = str(row['금액_문자열']).strip()
                
                parts = []
                if not hide_dong: parts.append(str(row['동']))
                if not hide_type: parts.append(str(row['타입']))
                parts.append(floor_txt)
                parts.append(price_str)
                if not hide_dir: parts.append(str(row['방향']))
                
                return "▪️ " + " / ".join(parts)

            if group_option == "🏢 동별 (기본 - 전체)":
                for dong in sorted(today_ls['동'].unique()):
                    sub = today_ls[today_ls['동'] == dong].sort_values('금액_하한(억)')
                    katalk_briefing += f"[🏢 {dong}]\n"
                    for _, row in sub.iterrows(): katalk_briefing += f"{format_briefing_item(row, hide_dong=True)}\n"
                    katalk_briefing += "\n"
            
            elif group_option == "📐 타입별 (전체)":
                for t_code in sorted(today_ls['타입'].unique()):
                    sub = today_ls[today_ls['타입'] == t_code].sort_values('금액_하한(억)')
                    katalk_briefing += f"[📐 {t_code} 타입]\n"
                    for _, row in sub.iterrows(): katalk_briefing += f"{format_briefing_item(row, hide_type=True)}\n"
                    katalk_briefing += "\n"

            elif group_option == "🧭 방향별 (전체)":
                for direction in sorted(today_ls['방향'].unique()):
                    sub = today_ls[today_ls['방향'] == direction].sort_values('금액_하한(억)')
                    katalk_briefing += f"[🧭 {direction}]\n"
                    for _, row in sub.iterrows(): katalk_briefing += f"{format_briefing_item(row, hide_dir=True)}\n"
                    katalk_briefing += "\n"

            else:
                katalk_briefing += "🔥 [타입별 최저가 매물 요약]\n"
                type_mins = today_ls.groupby('타입')['금액_하한(억)'].idxmin()
                for idx in type_mins:
                    r = today_ls.loc[idx]
                    raw_f = str(r['층']).split('/')[0] if '/' in str(r['층']) else str(r['층'])
                    f_txt = raw_f if str(raw_f).endswith('층') else f"{raw_f}층"
                    katalk_briefing += f"▪️ {r['타입']}: {r['동']} / {f_txt} / {r['금액_문자열']} / {r['방향']}\n"
                
                katalk_briefing += "\n🏆 [단지 최저가 Top 5 매물]\n"
                top5 = today_ls.sort_values('금액_하한(억)').head(5)
                for _, r in top5.iterrows():
                    raw_f = str(r['층']).split('/')[0] if '/' in str(r['층']) else str(r['층'])
                    f_txt = raw_f if str(raw_f).endswith('층') else f"{raw_f}층"
                    katalk_briefing += f"▪️ {r['동']} / {r['타입']} / {f_txt} / {r['금액_문자열']} / {r['방향']}\n"

            st.text_area("📋 아래 텍스트 전체를 복사하여 카카오톡으로 발송하세요", value=katalk_briefing, height=450)
        else: st.info("브리핑을 생성할 매매 호가 데이터가 없습니다.")

    # --- TAB 3: 전월세 & 정밀 층수 통제 갭 트래킹 ---
    with tab3:
        st.markdown(f"### 🔑 [{selected_complex}] 전월세 시세 & 층수그룹 정밀 갭(Gap) 분석")
        
        if not target_rn.empty:
            target_rn['수집일_dt'] = pd.to_datetime(target_rn['수집일'])
            latest_rn_dt = target_rn['수집일_dt'].max()
            today_rn = target_rn[target_rn['수집일_dt'] == latest_rn_dt].copy()
            today_rn['층_구분'] = today_rn['층'].apply(categorize_floor)
            
            jeonse_df = today_rn[today_rn['거래구분'] == '전세'].copy()
            
            if not jeonse_df.empty and not today_ls.empty:
                st.subheader("🎯 [층수 그룹 통제] 실전 체결 가능 갭 Matrix")
                
                jeonse_df['타입_그룹'] = jeonse_df['타입'].astype(str).str.extract(r'(\d+)')[0]
                
                sale_floor_min = today_ls.groupby(['타입_그룹', '층_구분'])['금액_하한(억)'].min().reset_index().rename(columns={'금액_하한(억)': '매매최저가(억)'})
                jeonse_floor_max = jeonse_df.groupby(['타입_그룹', '층_구분'])['보증금(억)'].max().reset_index().rename(columns={'보증금(억)': '전세최고가(억)'})
                
                gap_floor_df = pd.merge(sale_floor_min, jeonse_floor_max, on=['타입_그룹', '층_구분'], how='inner')
                gap_floor_df['실투자갭(억)'] = gap_floor_df['매매최저가(억)'] - gap_floor_df['전세최고가(억)']
                gap_floor_df['전세가율(%)'] = (gap_floor_df['전세최고가(억)'] / gap_floor_df['매매최저가(억)']) * 100
                
                st.dataframe(
                    gap_floor_df,
                    column_config={
                        "타입_그룹": "타입",
                        "층_구분": "층수 그룹",
                        "매매최저가(억)": st.column_config.NumberColumn("동일층 매매최저", format="%.2f 억"),
                        "전세최고가(억)": st.column_config.NumberColumn("동일층 전세최고", format="%.2f 억"),
                        "실투자갭(억)": st.column_config.NumberColumn("🔑 실전 투자갭", format="%.2f 억"),
                        "전세가율(%)": st.column_config.NumberColumn("전세가율", format="%.1f %%")
                    },
                    hide_index=True, use_container_width=True
                )
            else:
                st.info("💡 매매 호가와 전세 데이터가 동시에 수집되었을 때 층수그룹 정밀 갭 Matrix가 산출됩니다.")
            
            st.markdown("---")
            st.subheader("📋 전체 전월세 등록 매물 리스트")
            st.dataframe(
                today_rn.sort_values('보증금(억)', ascending=False)[['매물등록일', '동', '거래구분', '타입', '층', '방향', '금액_문자열', '중개사수']],
                use_container_width=True
            )
        else:
            st.info("등록된 전월세 데이터가 없습니다. 사이드바의 [⚙️ 스마트 데이터 입력]에서 전월세 원문을 입력해 보세요.")

    # --- TAB 4: 괴리율 & 체류기간 ---
    with tab4:
        st.markdown(f"### 📊 [{selected_complex}] 층수보정 괴리율 & 체류기간 (DOM)")
        if not today_ls.empty:
            display_cols = [c for c in ['동', '타입', '층', '층_구분', '방향', '금액_하한(억)', '최근실거래평균(억)', '층보정_괴리율(%)', 'DOM(일)', '중개사명'] if c in today_ls.columns]
            display_df = today_ls[display_cols].copy()
            st.dataframe(
                display_df.sort_values('DOM(일)', ascending=False),
                column_config={
                    "금액_하한(억)": st.column_config.NumberColumn("현재 호가", format="%.2f 억"),
                    "최근실거래평균(억)": st.column_config.NumberColumn("층별 실거래평균", format="%.2f 억"),
                    "층보정_괴리율(%)": st.column_config.NumberColumn("층보정 괴리율", format="%+.2f %%"),
                    "DOM(일)": st.column_config.ProgressColumn("매물 체류기간 (DOM)", format="%d 일", min_value=0, max_value=120),
                },
                hide_index=True, use_container_width=True
            )
        else: st.info("괴리율 분석을 위한 매매 호가 데이터가 없습니다.")

    # --- TAB 5: 시각화 차트 ---
    with tab5:
        st.markdown("### 📈 정밀 시각화 그래픽스 (시계열 밴드 / 히트맵 / 워터폴)")
        col_l, col_r = st.columns(2)
        with col_l:
            fig = go.Figure()
            if not target_ls.empty:
                ls_daily = target_ls.groupby('수집일')['금액_하한(억)'].agg(['min', 'mean', 'max']).reset_index()
                fig.add_trace(go.Scatter(x=ls_daily['수집일'], y=ls_daily['min'], name='최저 호가', line=dict(color='#10B981', width=3)))
                fig.add_trace(go.Scatter(x=ls_daily['수집일'], y=ls_daily['max'], name='최고 호가', line=dict(color='#EF4444', dash='dot')))
            
            if not target_tx.empty:
                tx_valid = target_tx.dropna(subset=['날짜', '금액_하한(억)']).sort_values('날짜')
                if not tx_valid.empty:
                    hover_labels = (
                        tx_valid['타입'].astype(str) + " / " + 
                        tx_valid['층'].astype(str) + "층 / " + 
                        tx_valid['금액_문자열'].astype(str)
                    )
                    fig.add_trace(go.Scatter(
                        x=tx_valid['날짜'], 
                        y=tx_valid['금액_하한(억)'], 
                        mode='markers', 
                        name='실거래 체결점', 
                        marker=dict(size=10, color='purple', symbol='diamond'),
                        hovertext=hover_labels
                    ))
                    
            fig.update_layout(title="시계열 호가 밴드 vs 실거래가", xaxis_title="날짜", yaxis_title="억 원", hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)

        with col_r:
            if not today_ls.empty:
                today_ls_copy = today_ls.copy()
                heatmap_data = today_ls_copy.pivot_table(index='층_구분', columns='타입', values='금액_하한(억)', aggfunc='min')
                floor_order = ['탑층', '고층', '중층', '저층']
                valid_order = [f for f in floor_order if f in heatmap_data.index]
                heatmap_data = heatmap_data.reindex(valid_order)

                if not heatmap_data.empty:
                    fig_hm = px.imshow(heatmap_data, labels=dict(x="타입(Type)", y="층수 그룹", color="최저 호가(억)"), color_continuous_scale="Blues", text_auto=".2f")
                    fig_hm.update_layout(title="층수 그룹 x 타입별 최저 호가 Matrix")
                    st.plotly_chart(fig_hm, use_container_width=True)
                else: st.info("히트맵 구성 데이터가 부족합니다.")
            else: st.info("매매 호가 데이터가 없어 히트맵을 생성할 수 없습니다.")

        st.markdown("---")
        st.subheader("📉 개별 매물 호가 인하 궤적 (Plotly Waterfall Chart)")
        if not today_ls.empty:
            cut_df = today_ls[today_ls['가격변동액(억)'] < 0].copy()
            if not cut_df.empty:
                cut_df['매물식별'] = cut_df['동'].astype(str) + " / " + cut_df['타입'].astype(str) + " / " + cut_df['층'].astype(str) + "층"
                selected_item = st.selectbox("호가 인하 매물 선택", cut_df['매물식별'].unique())
                target_item = cut_df[cut_df['매물식별'] == selected_item].iloc[0]
                first_p = target_item['최초호가(억)']
                cut_p = target_item['가격변동액(억)']
                curr_p = target_item['금액_하한(억)']

                fig_wf = go.Figure(go.Waterfall(
                    name="호가 흐름", orientation="v",
                    measure=["absolute", "relative", "total"],
                    x=["최초 등록 호가", "가격 인하액", "현재 최종 호가"],
                    textposition="outside",
                    text=[f"{first_p:.2f}억", f"{cut_p:.2f}억", f"{curr_p:.2f}억"],
                    y=[first_p, cut_p, curr_p],
                    connector={"line": {"color": "rgb(107, 114, 128)", "dash": "dot"}},
                    decreasing={"marker": {"color": "#EF4444"}},
                    totals={"marker": {"color": "#3B82F6"}}
                ))
                fig_wf.update_layout(title=f"[{selected_item}] 가격 인하 워터폴 트래킹", yaxis_title="억 원", showlegend=False)
                st.plotly_chart(fig_wf, use_container_width=True)
            else:
                st.info("💡 과거 수집 대비 호가를 인하한 매물이 감지되면 워터폴 차트가 자동 구성됩니다.")
        else: st.info("매매 호가 데이터가 없습니다.")

    # --- TAB 6: 원문 히스토리 ---
    with tab6:
        st.markdown("### 🔍 과거 입력 원문 히스토리")
        if os.path.exists(RAW_DB_PATH):
            raw_db = pd.read_csv(RAW_DB_PATH)
            target_raw = raw_db[raw_db['단지명'] == selected_complex]
            if not target_raw.empty:
                search_date = st.selectbox("날짜 선택", sorted(target_raw['날짜'].unique(), reverse=True))
                c1, c2, c3 = st.columns(3)
                with c1:
                    raw_tx = target_raw[(target_raw['날짜']==search_date) & (target_raw['유형']=='실거래')]
                    st.text_area("RTX (실거래)", raw_tx['원문'].iloc[0] if not raw_tx.empty else "기록 없음", height=350)
                with c2:
                    raw_ls = target_raw[(target_raw['날짜']==search_date) & (target_raw['유형']=='매매호가')]
                    st.text_area("RLS (매매)", raw_ls['원문'].iloc[0] if not raw_ls.empty else "기록 없음", height=350)
                with c3:
                    raw_rn = target_raw[(target_raw['날짜']==search_date) & (target_raw['유형']=='전월세')]
                    st.text_area("RRN (전월세)", raw_rn['원문'].iloc[0] if not raw_rn.empty else "기록 없음", height=350)
else:
    st.info("📌 수집된 데이터가 없습니다. 좌측 사이드바 [⚙️ 스마트 데이터 입력] 팝오버를 눌러 원문 텍스트를 입력해 보세요.")
