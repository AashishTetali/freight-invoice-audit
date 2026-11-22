@echo off
REM One complete run: install dashboard libraries, run the tests, build both datasets
REM with reproducibility checks, then open the dashboard. Double-click this file or run: run_all.bat
cd /d "%~dp0"

echo.
echo [1/4] Installing the dashboard libraries (pandas, streamlit)...
python -m pip install --quiet -r requirements.txt || goto :failed

echo.
echo [2/4] Running the automated tests...
python -m unittest || goto :failed

echo.
echo [3/4] Building the starter and full datasets + reproducibility checks (about 3 minutes)...
python -m freight_audit all || goto :failed

echo.
echo [4/4] Opening the dashboard in your browser. Press Ctrl+C in this window to stop it.
python -m streamlit run dashboard/app.py
goto :eof

:failed
echo.
echo Something failed above. Read the message, fix it, then run this file again.
pause
exit /b 1
