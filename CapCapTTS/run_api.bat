@REM @echo off
@REM echo Starting TTS API server...
@REM cd /d "%~dp0backend"
@REM python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload




@echo off
echo Starting TTS API server...
cd /d "%~dp0backend"
"C:\Users\hophu\AppData\Local\Programs\Python\Python312\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
