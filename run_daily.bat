@echo off
cd /d "C:\Users\user\Desktop\Hub\Edit\crawler"
echo [%date% %time%] 크롤러 시작 >> logs\scheduler.log
python run_parallel.py >> logs\scheduler.log 2>&1
echo [%date% %time%] 크롤러 종료 >> logs\scheduler.log
