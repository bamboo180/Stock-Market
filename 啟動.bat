@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 股票訊號看板 v1.9
echo 關閉舊版（如果還在執行）...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1
echo 檢查套件...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
  echo 安裝失敗，請確認已安裝 Python 3.10 以上，且安裝時有勾選 Add Python to PATH
  pause
  exit /b 1
)
python -m streamlit run app.py --server.port 8501
pause
