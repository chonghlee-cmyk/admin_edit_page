#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ADMIN EDIT PAGE
===============
"관리자 설정" 탭 A열 작품번호를 읽어,
각 작품별로 언어 페이지를 방문해 5개 필드를 수집하고
같은 탭 D열부터 기록한다. (A=작품번호, B=플랫폼, C=KR상태 보존)

수집 필드 (언어별):
  연재상태 (투믹스) : finish_yn_{fs}              (select)
  연재상태 (라라툰) : fmale_finish_yn_{fs}        (select)
  작품활성화 (투믹스): set_lang_display_{fs}       (checkbox)
  작품활성화 (라라툰): set_lang_fmale_display_{fs} (checkbox)
  요일              : {fs}_d1~d7, {fs}_d10        (checkbox 조합)
  최종상태          : (수식 열 — 크롤러가 건드리지 않음)

사용법:
  python admin_edit_page.py               # 전체 실행
  python admin_edit_page.py --debug       # 첫 번째 작품 EN 페이지 분석
  python admin_edit_page.py --start 0 --end 100  # 범위 지정
"""

import os
import sys
import time
import random
import socket
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

import gspread
from oauth2client.service_account import ServiceAccountCredentials

socket.setdefaulttimeout(15)


# =============================================================================
# CONFIG
# =============================================================================
BASE_URL  = os.environ.get("BASE_URL", "https://ztmng-g.toomics.com")
LOGIN_URL = f"{BASE_URL}/auth/login"
MOD_URL   = f"{BASE_URL}/contents_multi/contents_mod_{{lang}}/toon_idx/{{toon_idx}}"

ADMIN_ID  = os.environ.get("ADMIN_ID", "")
ADMIN_PWD = os.environ.get("ADMIN_PWD", "")

SPREADSHEET_ID       = os.environ.get("SPREADSHEET_ID", "")
WORKSHEET_NAME       = "관리자 설정"
LOG_WORKSHEET_NAME   = "관리자 설정 로그"
SERVICE_ACCOUNT_JSON = os.environ.get("SERVICE_ACCOUNT_JSON", "")

DEBUG_DIR = Path("debug")
DEBUG_DIR.mkdir(exist_ok=True)

REQUEST_TIMEOUT = (5, 10)
SLEEP_BASE      = 0.1
SLEEP_JITTER    = 0.05

GSCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

LANGUAGES: List[Tuple[str, str, str, str]] = [
    # (coin_prefix, summary_prefix, url_suffix, field_suffix)
    ("EN", "EN", "en",  "en"),
    ("FR", "FR", "fr",  "fr"),
    ("ES", "ES", "es",  "es_mx"),
    ("PT", "PT", "pt",  "pt_br"),
    ("IT", "IT", "it",  "it"),
    ("DE", "DE", "de",  "de"),
    ("ZH", "TW", "zh",  "zh_tw"),
    ("JP", "JP", "jp",  "jp"),
    ("TH", "TH", "th",  "th"),
]

FIELD_NAMES = ["연재상태G", "연재상태라라", "활성화G", "활성화라라", "요일", "계약종료투믹스", "계약종료라라"]

# A=작품번호, B=플랫폼, C=KR상태 → 크롤 데이터는 D열(4)부터
LANG_COL_START   = 4   # D열
FIELDS_PER_LANG  = 7   # 7개 데이터 (5개 + 2개)
DATA_FIELDS      = 7   # 실제 기록할 필드 수

LANG_CHUNK_SIZE  = 100  # 언어 탭 일괄 쓰기 버퍼 크기 (행)

# 언어별 HTML 필드명 매핑 (url_suffix → html_field_name)
LANGUAGE_HTML_MAP = {
    "en":     "en",
    "fr":     "fr",
    "es":     "spanish(la)",      # ES는 spanish(la) 사용
    "pt":     "pt_br",
    "it":     "it",
    "de":     "de",
    "zh":     "taiwan",           # ZH는 taiwan 사용
    "jp":     "jp",
    "th":     "th",
}


# =============================================================================
# HTTP
# =============================================================================
def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "close",
    })
    retry = Retry(total=0, raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def login(session: requests.Session) -> None:
    payload = {"admin_id": ADMIN_ID, "admin_pwd": ADMIN_PWD, "return": ""}
    headers = {"Referer": f"{BASE_URL}/auth/login?return="}
    resp = session.post(LOGIN_URL, data=payload, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    if "logout" not in resp.text.lower() and "admin_id" in resp.text.lower():
        print("[LOGIN] WARNING: 로그인 실패 가능성 — 계속 진행")
    else:
        print("[LOGIN] 로그인 성공")


def fetch_html(session: requests.Session, lang: str, toon_idx: str) -> Optional[str]:
    url = MOD_URL.format(lang=lang, toon_idx=toon_idx)
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=False)
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location", "")
            if "contents_list" in location or "auth/login" in location:
                return None
            resp = session.get(location, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        if "auth/login" in resp.url or "contents_list" in resp.url:
            return None
        return resp.text
    except Exception as e:
        print(f"  [!] {lang}/{toon_idx} 요청 실패: {e}")
        return None


# =============================================================================
# HTML 파싱
# =============================================================================
def _selected_text(soup: BeautifulSoup, select_name: str) -> str:
    sel = soup.find("select", attrs={"name": select_name})
    if not sel:
        return ""
    opt = sel.find("option", selected=True)
    if opt is None:
        opt = sel.find("option")
    if opt is None:
        return ""
    return opt.get_text(strip=True)


def _checkbox_checked(soup: BeautifulSoup, input_id: str) -> str:
    el = soup.find("input", id=input_id)
    if not el:
        return ""
    return "Y" if el.has_attr("checked") else "N"


def _selected_value(soup: BeautifulSoup, select_name: str) -> Optional[str]:
    """선택된 option의 value 반환. select 자체가 없으면 None."""
    sel = soup.find("select", attrs={"name": select_name})
    if not sel:
        return None
    opt = sel.find("option", selected=True)
    if opt is None:
        opt = sel.find("option")
    if opt is None:
        return None
    return opt.get("value", "").strip()


def _contract_result(soup: BeautifulSoup, status_names: List[str], date_names: List[str]) -> str:
    """
    계약 상태 + 종료일 조합 결과.
      · select(계약상태)가 없으면  → ""  (해당 플랫폼 필드 자체 없음)
      · value == "0" (정상/Safe)   → "정상"
      · value 1/2/3 (서비스 불가)   → 계약 종료일 (없으면 "")
    """
    status_val = None
    for nm in status_names:
        v = _selected_value(soup, nm)
        if v is not None:
            status_val = v
            break

    if status_val is None:
        return ""          # 필드 자체가 없음
    if status_val == "0":
        return "정상"       # Safe → 날짜 출력 안 함

    # 이슈(서비스 불가) → 종료일 출력
    for nm in date_names:
        inp = soup.find("input", attrs={"name": nm})
        if inp and inp.has_attr("value"):
            val = inp.get("value", "").strip()
            if val:
                return val
    return ""


def parse_lang_fields(soup: BeautifulSoup, fs: str, html_fs: str) -> Dict[str, str]:
    status_g    = _selected_text(soup, select_name=f"finish_yn_{fs}")
    status_lala = _selected_text(soup, select_name=f"fmale_finish_yn_{fs}")
    active_g    = _checkbox_checked(soup, input_id=f"set_lang_display_{fs}")
    active_lala = _checkbox_checked(soup, input_id=f"set_lang_fmale_display_{fs}")

    day_map = {1: "월", 2: "화", 3: "수", 4: "목", 5: "금", 6: "토", 7: "일", 10: "매일"}
    checked_days = []
    for d_num, d_name in day_map.items():
        inp = soup.find("input", attrs={"name": f"{fs}_d{d_num}"})
        if inp and inp.has_attr("checked"):
            checked_days.append(d_name)
    days = ", ".join(checked_days) if checked_days else "미설정"

    # 계약 종료 필드 — 접미사가 언어마다 다를 수 있어 후보를 순서대로 시도
    # (예: ES는 es_mx 또는 spanish(la), ZH는 zh_tw 또는 taiwan)
    suffix_candidates = []
    for s in (fs, html_fs):
        if s and s not in suffix_candidates:
            suffix_candidates.append(s)

    # 투믹스(기본) 계약: contract_status_{s} / contract_end_date_{s}
    contract_end_date_g = _contract_result(
        soup,
        status_names=[f"contract_status_{s}" for s in suffix_candidates],
        date_names=[f"contract_end_date_{s}" for s in suffix_candidates],
    )

    # 라라툰(fmale) 계약: fmale_contract_status_{s} / fmale_contract_end_date_{s}
    lala_status_names = []
    lala_date_names = []
    for s in suffix_candidates:
        lala_status_names += [f"fmale_contract_status_{s}", f"contract_status_fmale_{s}"]
        lala_date_names   += [f"fmale_contract_end_date_{s}", f"contract_end_date_fmale_{s}"]
    contract_end_date_lala = _contract_result(soup, lala_status_names, lala_date_names)

    return {
        "연재상태G":   status_g,
        "연재상태라라": status_lala,
        "활성화G":     "활성화" if active_g == "Y" else ("비활성화" if active_g == "N" else ""),
        "활성화라라":  "활성화" if active_lala == "Y" else ("비활성화" if active_lala == "N" else ""),
        "요일":        days,
        "계약종료투믹스": contract_end_date_g,
        "계약종료라라": contract_end_date_lala,
    }


# =============================================================================
# Google Sheets
# =============================================================================
def open_or_create_log_worksheet(sh):
    try:
        return sh.worksheet(LOG_WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=LOG_WORKSHEET_NAME, rows=1000, cols=5)
        ws.append_row(["실행일", "실행 시간", "소요 시간", "처리 수", "워커/언어"], value_input_option="RAW")
        return ws


def read_sheet_meta(ws) -> List[str]:
    """A2: 읽어 작품번호 리스트 반환."""
    values = ws.get("A2:A")
    result = []
    for row in values:
        toon_id = (row[0] if len(row) > 0 else "").strip()
        if toon_id:
            result.append(toon_id)
    return result


def build_lang_headers(sum_pfx: str) -> List[str]:
    """단일 언어 탭의 D열부터 헤더 (7개 필드)."""
    return [
        f"{sum_pfx} 연재 상태 (투믹스)",
        f"{sum_pfx} 연재 상태 (라라툰)",
        f"{sum_pfx} 작품 활성화 (투믹스)",
        f"{sum_pfx} 작품 활성화 (라라툰)",
        f"{sum_pfx} 요일",
        f"{sum_pfx} 계약 종료 (투믹스)",
        f"{sum_pfx} 계약 종료 (라라툰)",
    ]


def build_headers() -> List[str]:
    """D열부터 시작하는 전체 언어 헤더 (A/B/C는 건드리지 않음)."""
    headers = []
    for _, sum_pfx, _, _ in LANGUAGES:
        headers += build_lang_headers(sum_pfx)
    return headers


def write_header(ws, headers: List[str]) -> None:
    # D1부터 작성 — A/B/C 보존
    start_cell = gspread.utils.rowcol_to_a1(1, LANG_COL_START)
    ws.update(range_name=start_cell, values=[headers], value_input_option="RAW")
    print(f"[SHEET] 헤더 {len(headers)}개 컬럼 작성 완료 (D1~)")


def write_row(ws, sheet_row: int, lang_results: List[List[str]]) -> None:
    """
    각 언어별 5개 데이터를 해당 열에 기록.
    최종상태 열(6번째)은 건드리지 않음 — batch_update로 언어별 범위 분리.
    """
    update_data = []
    for lang_idx, fields_5 in enumerate(lang_results):
        col_start = LANG_COL_START + lang_idx * FIELDS_PER_LANG
        col_end   = col_start + DATA_FIELDS - 1
        a1 = gspread.utils.rowcol_to_a1(sheet_row, col_start)
        a2 = gspread.utils.rowcol_to_a1(sheet_row, col_end)
        update_data.append({"range": f"{a1}:{a2}", "values": [fields_5]})

    for attempt in range(5):
        try:
            ws.batch_update(update_data, value_input_option="RAW")
            return
        except gspread.exceptions.APIError as e:
            if "RESOURCE_EXHAUSTED" in str(e) or "Quota" in str(e):
                wait = 60 * (attempt + 1)
                print(f"  [!] Sheets API 할당량 초과 — {wait}초 대기 후 재시도 ({attempt+1}/5)")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"write_row 실패: 5회 재시도 초과 (row {sheet_row})")


def flush_lang_buffer(ws, start_row: int, rows: List[List[str]], label: str = "") -> None:
    """
    버퍼된 연속 행(D{start_row}~)을 한 번의 API 호출로 일괄 기록.
    쿼터 초과(RESOURCE_EXHAUSTED/429) 시 지수 백오프 재시도.
    """
    if not rows:
        return
    a1 = gspread.utils.rowcol_to_a1(start_row, LANG_COL_START)
    a2 = gspread.utils.rowcol_to_a1(start_row + len(rows) - 1, LANG_COL_START + DATA_FIELDS - 1)
    rng = f"{a1}:{a2}"

    for attempt in range(6):
        try:
            ws.update(range_name=rng, values=rows, value_input_option="RAW")
            return
        except gspread.exceptions.APIError as e:
            msg = str(e)
            if "RESOURCE_EXHAUSTED" in msg or "Quota" in msg or "429" in msg:
                wait = min(60, 5 * (2 ** attempt))   # 5,10,20,40,60,60
                print(f"  [!] [{label}] 쿼터 초과 — {wait}초 대기 후 재시도 ({attempt+1}/6)")
                time.sleep(wait)
            else:
                raise
    print(f"  [!] [{label}] 일괄 기록 실패: 재시도 초과 (시작행 {start_row}, {len(rows)}행)")


def write_sync_log(sh, processed: int, label, duration_sec: float) -> None:
    """label: 언어 코드(EN…) 또는 워커 번호."""
    from datetime import datetime
    try:
        log_ws = open_or_create_log_worksheet(sh)
        now = datetime.now()
        sec = int(duration_sec)
        duration_str = f"{sec // 60}분 {sec % 60}초" if sec >= 60 else f"{sec}초"
        log_ws.append_row(
            [now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), duration_str, processed, label],
            value_input_option="RAW",
        )
        print(f"[LOG] 로그 기록 완료 → '{LOG_WORKSHEET_NAME}' ({label})")
    except Exception as e:
        print(f"[LOG] 로그 기록 실패: {e}")


# =============================================================================
# MAIN
# =============================================================================
def main() -> None:
    import time as _time
    start_time = _time.time()

    debug_mode = "--debug" in sys.argv

    def _get_arg(flag, default=None):
        if flag in sys.argv:
            try:
                idx = sys.argv.index(flag)
                val = sys.argv[idx + 1]
                # 정수 파싱 시도
                try:
                    return int(val)
                except ValueError:
                    # 정수가 아니면 문자열 반환
                    return val
            except (IndexError, ValueError):
                pass
        return default

    target_lang = _get_arg("--lang", None)
    start_idx = _get_arg("--start", 0)
    end_idx   = _get_arg("--end", None)
    worker_id = _get_arg("--worker", 1)

    if isinstance(start_idx, str):
        start_idx = 0
    if isinstance(end_idx, str):
        end_idx = None

    print("=" * 55)
    if target_lang:
        print(f"  ADMIN EDIT PAGE Crawler  [{target_lang}]")
    else:
        print(f"  ADMIN EDIT PAGE Crawler  [worker {worker_id}]")
        print(f"  range: {start_idx} ~ {end_idx or 'end'}")
    print("=" * 55)

    print("[SHEET] 연결 중...")
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_JSON, GSCOPE)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    # target_lang이 있으면 해당 언어 탭에서, 아니면 관리자 설정에서 읽기
    if target_lang:
        try:
            ws = sh.worksheet(target_lang)
            print(f"[SHEET] 언어 탭: {target_lang}")
        except gspread.exceptions.WorksheetNotFound:
            print(f"[!] '{target_lang}' 탭을 찾을 수 없습니다.")
            return
    else:
        ws = sh.worksheet(WORKSHEET_NAME)

    all_toon_ids = read_sheet_meta(ws)
    print(f"[SHEET] 전체 작품번호 {len(all_toon_ids)}개 로드")
    if not all_toon_ids:
        print("[!] 작품번호 없음 - A열에 작품번호를 입력해주세요.")
        return

    if target_lang:
        toon_ids = all_toon_ids
        print(f"[SHEET] {target_lang} 탭 처리: {len(toon_ids)}개")
    else:
        toon_ids = all_toon_ids[start_idx:end_idx]
        print(f"[SHEET] 담당 범위: {start_idx}~{(end_idx or len(all_toon_ids)) - 1} ({len(toon_ids)}개)")

    session = build_session()
    login(session)

    if debug_mode:
        toon_idx = toon_ids[0]
        print(f"\n[DEBUG] 작품번호 {toon_idx}")
        for _, sum_pfx, url_suffix, field_suffix in LANGUAGES:
            html_fs = LANGUAGE_HTML_MAP.get(url_suffix, field_suffix)
            html = fetch_html(session, url_suffix, toon_idx)
            if not html:
                print(f"  [{sum_pfx}] 로드 실패")
                continue
            soup = BeautifulSoup(html, "html.parser")
            fields = parse_lang_fields(soup, field_suffix, html_fs)
            print(f"  [{sum_pfx}] {fields}")
        return

    # 헤더 작성
    if target_lang:
        # 해당 언어의 sum_pfx 찾아서 7개 헤더만 D1부터 작성
        sum_pfx = next(
            (sp for cp, sp, _, _ in LANGUAGES if cp == target_lang.upper()),
            target_lang.upper(),
        )
        write_header(ws, build_lang_headers(sum_pfx))

        # 옛 레이아웃 잔재 정리 — 7필드 뒤(K열~)에 남은 예전 데이터/헤더 삭제
        leftover_start = LANG_COL_START + DATA_FIELDS  # 4 + 7 = 11 (K열)
        last_col = ws.col_count
        if last_col >= leftover_start:
            a1 = gspread.utils.rowcol_to_a1(1, leftover_start)
            a2 = gspread.utils.rowcol_to_a1(len(toon_ids) + 1, last_col)
            try:
                ws.batch_clear([f"{a1}:{a2}"])
                print(f"[SHEET] 옛 잔재 정리 ({a1}:{a2})")
            except Exception as e:
                print(f"[SHEET] 잔재 정리 실패: {e}")
    elif worker_id == 1:
        write_header(ws, build_headers())

    # 시트 행 번호: 헤더(1행) + start_idx 오프셋
    toon_id_to_sheet_row = {tid: (2 + start_idx + j) for j, tid in enumerate(toon_ids)}

    total     = len(toon_ids)
    processed = 0

    # 언어 탭 일괄 쓰기 버퍼 (연속 행을 모아 한 번에 기록)
    buf: List[List[str]] = []
    buf_start_row: Optional[int] = None

    for i, toon_idx in enumerate(toon_ids, start=1):
        # 작품번호마다 TCP 연결 풀 초기화 (hang 방지)
        old_jar = session.cookies.copy()
        session.close()
        session = build_session()
        session.cookies = old_jar

        if target_lang:
            # 특정 언어만 처리 (--lang 플래그)
            lang_ok = 0
            for lang_tuple in LANGUAGES:
                coin_pfx, sum_pfx, url_suffix, field_suffix = lang_tuple
                # 해당 언어만 선택
                if coin_pfx != target_lang.upper():
                    continue

                html_fs = LANGUAGE_HTML_MAP.get(url_suffix, field_suffix)
                html = fetch_html(session, url_suffix, toon_idx)
                if html:
                    soup = BeautifulSoup(html, "html.parser")
                    fields = parse_lang_fields(soup, field_suffix, html_fs)
                    lang_ok += 1
                else:
                    fields = {k: "" for k in FIELD_NAMES}

                # 언어 탭에 D열부터 7개 필드만 기록 (A/B/C는 Apps Script 담당 — 보존)
                sheet_row = i + 1  # 헤더(1행) + 데이터 시작
                field_data = [fields.get(fname, "") for fname in FIELD_NAMES]

                # 버퍼에 적재 (연속 행) — 가득 차면 일괄 플러시
                if buf_start_row is None:
                    buf_start_row = sheet_row
                buf.append(field_data)
                if len(buf) >= LANG_CHUNK_SIZE:
                    flush_lang_buffer(ws, buf_start_row, buf, target_lang)
                    buf = []
                    buf_start_row = None

                time.sleep(SLEEP_BASE + random.random() * SLEEP_JITTER)
                break  # 해당 언어 처리 후 종료

            processed += 1
            if i <= 3 or i == total or i % 50 == 0:
                print(f"[{target_lang}] [{i}/{total}] {toon_idx} - {lang_ok}/1 OK")
        else:
            # 기존 방식 (모든 언어 처리)
            lang_results: List[List[str]] = []
            lang_ok = 0

            for _, _, url_suffix, field_suffix in LANGUAGES:
                html_fs = LANGUAGE_HTML_MAP.get(url_suffix, field_suffix)
                html = fetch_html(session, url_suffix, toon_idx)
                if html:
                    soup = BeautifulSoup(html, "html.parser")
                    fields = parse_lang_fields(soup, field_suffix, html_fs)
                    lang_ok += 1
                else:
                    fields = {k: "" for k in FIELD_NAMES}

                lang_results.append([fields.get(fname, "") for fname in FIELD_NAMES])
                time.sleep(SLEEP_BASE + random.random() * SLEEP_JITTER)

            sheet_row = toon_id_to_sheet_row[toon_idx]
            try:
                write_row(ws, sheet_row, lang_results)
            except Exception as e:
                print(f"  [!] 시트 기록 실패 {toon_idx}: {e}")

            processed += 1
            if i <= 3 or i == total or i % 50 == 0:
                print(f"[W{worker_id}] [{i}/{total}] {toon_idx} - {lang_ok}/{len(LANGUAGES)} OK")

    # 버퍼에 남은 잔여 행 일괄 플러시
    if target_lang and buf:
        flush_lang_buffer(ws, buf_start_row, buf, target_lang)
        buf = []
        buf_start_row = None

    duration = _time.time() - start_time
    log_label = target_lang.upper() if target_lang else worker_id
    print(f"\n[{log_label}] 완료! ({processed}개 처리)")
    write_sync_log(sh, processed, log_label, duration)


if __name__ == "__main__":
    main()
