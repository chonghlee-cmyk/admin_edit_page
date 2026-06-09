"""
run_parallel.py — admin_edit_page.py 를 언어별로 병렬 실행.

사용법:
  python run_parallel.py              # 9개 언어 에이전트 병렬 실행
"""

import subprocess
import sys
import os
import time
from pathlib import Path

SCRIPT = Path(__file__).parent / "admin_edit_page.py"
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

LANGUAGES = ['EN', 'FR', 'ES', 'PT', 'IT', 'DE', 'ZH', 'JP', 'TH']


def main():
    print(f"언어 에이전트 {len(LANGUAGES)}개 시작")
    print("=" * 55)

    procs = []
    for lang in LANGUAGES:
        log_path = LOG_DIR / f"{lang}.log"
        cmd = [
            sys.executable, "-u", str(SCRIPT),
            "--lang", lang,
        ]

        log_file = open(log_path, "w", encoding="utf-8")
        proc = subprocess.Popen(cmd, stdout=log_file, stderr=log_file,
                                 cwd=str(SCRIPT.parent))
        procs.append((lang, proc, log_file))
        print(f"  [{lang}]  (PID {proc.pid})")

    print("\n실행 중... (Ctrl+C로 중단)\n")

    t_start = time.time()
    try:
        while True:
            alive = [p for _, p, _ in procs if p.poll() is None]
            done  = [lang for lang, p, _ in procs if p.poll() is not None]
            elapsed = int(time.time() - t_start)
            mm, ss = elapsed // 60, elapsed % 60

            # 상태 출력
            print(f"\r[{mm:02d}:{ss:02d}] 완료:{len(done)}/{len(procs)}  ", end="")

            if not alive:
                break
            time.sleep(5)

            # 5초마다 진행 현황 출력
            print()
            for lang, proc, _ in procs:
                log_path = LOG_DIR / f"{lang}.log"
                try:
                    lines = log_path.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
                    last = lines[-1] if lines else "(대기 중)"
                except Exception:
                    last = "(읽기 실패)"
                status = "완료" if proc.poll() is not None else "실행중"
                print(f"  [{lang}] {status} | {last[-70:]}")

    except KeyboardInterrupt:
        print("\n\n중단 요청 — 에이전트 종료 중...")
        for _, p, _ in procs:
            p.terminate()

    # 파일 핸들 닫기
    for _, _, f in procs:
        f.close()

    elapsed = int(time.time() - t_start)
    mm, ss = elapsed // 60, elapsed % 60
    print(f"\n\n전체 완료: {mm}분 {ss}초")

    # 각 언어 결과 요약
    print("\n[언어별 결과]")
    for lang, proc, _ in procs:
        log_path = LOG_DIR / f"{lang}.log"
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
            last_lines = text.strip().splitlines()[-2:]
            summary = " | ".join(l.strip() for l in last_lines)
        except Exception:
            summary = "(로그 없음)"
        code = proc.returncode
        status = "OK" if code == 0 else "FAIL"
        print(f"  [{lang}] {status} (exit {code}): {summary[-90:]}")


if __name__ == "__main__":
    main()
