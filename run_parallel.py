"""
run_parallel.py — admin_edit_page.py 를 여러 워커로 병렬 실행.

사용법:
  python run_parallel.py              # 기본 10 워커
  python run_parallel.py --workers 5  # 5 워커
  python run_parallel.py --force      # 기존 완료 항목 재처리
"""

import subprocess
import sys
import os
import time
import math
from pathlib import Path

SCRIPT = Path(__file__).parent / "admin_edit_page.py"
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

TOTAL = 2001


def get_arg(flag, default):
    if flag in sys.argv:
        try:
            return int(sys.argv[sys.argv.index(flag) + 1])
        except (IndexError, ValueError):
            pass
    return default


def main():
    n_workers = get_arg("--workers", 10)
    force = "--force" in sys.argv

    chunk = math.ceil(TOTAL / n_workers)
    ranges = []
    for i in range(n_workers):
        start = i * chunk
        end = min(start + chunk, TOTAL)
        if start >= TOTAL:
            break
        ranges.append((i + 1, start, end))

    print(f"워커 {len(ranges)}개 시작 (총 {TOTAL}개, 워커당 ~{chunk}개)")
    print("=" * 55)

    procs = []
    for worker_id, start, end in ranges:
        log_path = LOG_DIR / f"w{worker_id}.log"
        cmd = [
            sys.executable, "-u", str(SCRIPT),
            "--start", str(start),
            "--end", str(end),
            "--worker", str(worker_id),
        ]
        if force:
            cmd.append("--force")

        log_file = open(log_path, "w", encoding="utf-8")
        proc = subprocess.Popen(cmd, stdout=log_file, stderr=log_file,
                                 cwd=str(SCRIPT.parent))
        procs.append((worker_id, start, end, proc, log_file))
        print(f"  W{worker_id}: {start}~{end}  (PID {proc.pid})")

    print("\n실행 중... (Ctrl+C로 중단)\n")

    t_start = time.time()
    try:
        while True:
            alive = [p for _, _, _, p, _ in procs if p.poll() is None]
            done  = [wid for wid, _, _, p, _ in procs if p.poll() is not None]
            elapsed = int(time.time() - t_start)
            mm, ss = elapsed // 60, elapsed % 60

            # 각 워커 마지막 줄 출력
            print(f"\r[{mm:02d}:{ss:02d}] 완료:{len(done)}/{len(procs)} 워커  ", end="")

            if not alive:
                break
            time.sleep(10)

            # 10초마다 진행 현황 출력
            print()
            for wid, start, end, proc, _ in procs:
                log_path = LOG_DIR / f"w{wid}.log"
                try:
                    lines = log_path.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
                    last = lines[-1] if lines else "(대기 중)"
                except Exception:
                    last = "(읽기 실패)"
                status = "완료" if proc.poll() is not None else "실행중"
                print(f"  W{wid}[{status}] {last[-80:]}")

    except KeyboardInterrupt:
        print("\n\n중단 요청 — 워커 종료 중...")
        for _, _, _, p, _ in procs:
            p.terminate()

    # 파일 핸들 닫기
    for _, _, _, _, f in procs:
        f.close()

    elapsed = int(time.time() - t_start)
    mm, ss = elapsed // 60, elapsed % 60
    print(f"\n\n전체 완료: {mm}분 {ss}초")

    # 각 워커 결과 요약
    print("\n[워커별 결과]")
    for wid, start, end, proc, _ in procs:
        log_path = LOG_DIR / f"w{wid}.log"
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
            last_lines = text.strip().splitlines()[-3:]
            summary = " | ".join(l.strip() for l in last_lines)
        except Exception:
            summary = "(로그 없음)"
        code = proc.returncode
        print(f"  W{wid} (exit {code}): {summary[-100:]}")


if __name__ == "__main__":
    main()
