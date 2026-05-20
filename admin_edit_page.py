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

FIELD_NAMES = ["연재상태G", "연재상태라라", "활성화G", "활성화라라", "요일"]

# A=작품번호, B=플랫폼, C=KR상태 → 크롤 데이터는 D열(4)부터
LANG_COL_START   = 4   # D열
FIELDS_PER_LANG  = 5   # 5개 데이터
DATA_FIELDS      = 5   # 실제 기록할 필드 수


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


def parse_lang_fields(soup: BeautifulSoup, fs: str) -> Dict[str, str]:
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

    return {
        "연재상태G":   status_g,
        "연재상태라라": status_lala,
        "활성화G":     "활성화" if active_g == "Y" else ("비활성화" if active_g == "N" else ""),
        "활성화라라":  "활성화" if active_lala == "Y" else ("비활성화" if active_lala == "N" else ""),
        "요일":        days,
    }


# =============================================================================
# Google Sheets
# =============================================================================
def open_or_create_log_worksheet(sh):
    try:
        return sh.worksheet(LOG_WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=LOG_WORKSHEET_NAME, rows=1000, cols=5)
        ws.append_row(["실행일", "실행 시간", "소요 시간", "처리 수", "워커"], value_input_option="RAW")
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


def build_headers() -> List[str]:
    """D열부터 시작하는 언어별 헤더 (A/B/C는 건드리지 않음)."""
    headers = []
    for _, sum_pfx, _, _ in LANGUAGES:
        headers += [
            f"{sum_pfx} 연재 상태 (투믹스)",
            f"{sum_pfx} 연재 상태 (라라툰)",
            f"{sum_pfx} 작품 활성화 (투믹스)",
            f"{sum_pfx} 작품 활성화 (라라툰)",
            f"{sum_pfx} 요일",
        ]
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


def write_sync_log(sh, processed: int, worker_id: int, duration_sec: float) -> None:
    from datetime import datetime
    try:
        log_ws = open_or_create_log_worksheet(sh)
        now = datetime.now()
        sec = int(duration_sec)
        duration_str = f"{sec // 60}분 {sec % 60}초" if sec >= 60 else f"{sec}초"
        log_ws.append_row(
            [now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), duration_str, processed, worker_id],
            value_input_option="RAW",
        )
        print(f"[LOG] 로그 기록 완료 → '{LOG_WORKSHEET_NAME}'")
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
                return int(sys.argv[sys.argv.index(flag) + 1])
            except (IndexError, ValueError):
                pass
        return default

    start_idx = _get_arg("--start", 0)
    end_idx   = _get_arg("--end", None)
    worker_id = _get_arg("--worker", 1)

    print("=" * 55)
    print(f"  ADMIN EDIT PAGE Crawler  [worker {worker_id}]")
    print(f"  range: {start_idx} ~ {end_idx or 'end'}")
    print("=" * 55)

    print("[SHEET] 연결 중...")
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_JSON, GSCOPE)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet(WORKSHEET_NAME)

    all_toon_ids = read_sheet_meta(ws)
    print(f"[SHEET] 전체 작품번호 {len(all_toon_ids)}개 로드")
    if not all_toon_ids:
        print("[!] 작품번호 없음 - A열에 작품번호를 입력해주세요.")
        return

    toon_ids = all_toon_ids[start_idx:end_idx]
    print(f"[SHEET] 담당 범위: {start_idx}~{(end_idx or len(all_toon_ids)) - 1} ({len(toon_ids)}개)")

    session = build_session()
    login(session)

    if debug_mode:
        toon_idx = toon_ids[0]
        print(f"\n[DEBUG] 작품번호 {toon_idx}")
        for _, sum_pfx, url_suffix, field_suffix in LANGUAGES:
            html = fetch_html(session, url_suffix, toon_idx)
            if not html:
                print(f"  [{sum_pfx}] 로드 실패")
                continue
            soup = BeautifulSoup(html, "html.parser")
            fields = parse_lang_fields(soup, field_suffix)
            print(f"  [{sum_pfx}] {fields}")
        return

    # worker 1만 헤더 작성
    if worker_id == 1:
        headers = build_headers()
        write_header(ws, headers)

    # 시트 행 번호: 헤더(1행) + start_idx 오프셋
    toon_id_to_sheet_row = {tid: (2 + start_idx + j) for j, tid in enumerate(toon_ids)}

    total     = len(toon_ids)
    processed = 0

    for i, toon_idx in enumerate(toon_ids, start=1):
        # 작품번호마다 TCP 연결 풀 초기화 (hang 방지)
        old_jar = session.cookies.copy()
        session.close()
        session = build_session()
        session.cookies = old_jar

        lang_results: List[List[str]] = []
        lang_ok = 0

        for _, _, url_suffix, field_suffix in LANGUAGES:
            html = fetch_html(session, url_suffix, toon_idx)
            if html:
                soup = BeautifulSoup(html, "html.parser")
                fields = parse_lang_fields(soup, field_suffix)
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

    duration = _time.time() - start_time
    print(f"\n[W{worker_id}] 완료! ({processed}개 처리)")
    write_sync_log(sh, processed, worker_id, duration)


if __name__ == "__main__":
    main()
