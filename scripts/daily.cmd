@echo off
cd /d "C:\Users\shann\seismograph"
if not exist logs mkdir logs
echo ===== %date% %time% >> logs\daily.txt
".venv\Scripts\python.exe" -m seismograph check >> logs\daily.txt 2>&1
".venv\Scripts\python.exe" -m seismograph warn-all >> logs\daily.txt 2>&1
".venv\Scripts\python.exe" -m seismograph erm --days 14 >> logs\daily.txt 2>&1
".venv\Scripts\python.exe" -m seismograph run --days 3 --max-docs 25 >> logs\daily.txt 2>&1
".venv\Scripts\python.exe" -m seismograph run --days 3 --forms 20-F --max-docs 5 >> logs\daily.txt 2>&1
".venv\Scripts\python.exe" -m seismograph run --days 3 --forms 40-F --max-docs 5 >> logs\daily.txt 2>&1
".venv\Scripts\python.exe" -m seismograph uk --days 3 --max-docs 10 >> logs\daily.txt 2>&1
".venv\Scripts\python.exe" -m seismograph foreign --source all --days 2 --max-docs 8 >> logs\daily.txt 2>&1
".venv\Scripts\python.exe" -m seismograph spenders --scan --days 7 --max-docs 10 >> logs\daily.txt 2>&1
".venv\Scripts\python.exe" -m seismograph outcomes >> logs\daily.txt 2>&1
".venv\Scripts\python.exe" -m seismograph playbook >> logs\daily.txt 2>&1
".venv\Scripts\python.exe" -m seismograph scorecard >> logs\daily.txt 2>&1
".venv\Scripts\python.exe" -m seismograph countries >> logs\daily.txt 2>&1
".venv\Scripts\python.exe" -m seismograph publish >> logs\daily.txt 2>&1
git add docs out >> logs\daily.txt 2>&1
git commit -m "Daily update %date%" >> logs\daily.txt 2>&1
git push >> logs\daily.txt 2>&1
