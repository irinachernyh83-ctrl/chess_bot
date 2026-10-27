@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if not exist ".env" copy ".env.example" ".env"
echo.
echo Готово. Впиши токен в .env и запусти run.bat
pause
