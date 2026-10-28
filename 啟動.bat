@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 第一次執行會安裝套件，請稍候...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
  echo 安裝失敗，請確認已安裝 Python 3.10 以上，且安裝時有勾選 Add Python to PATH
  pause
  exit /b 1
)
python -m streamlit run app.py
pause
