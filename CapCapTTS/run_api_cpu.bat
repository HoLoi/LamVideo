@REM @echo off
@REM echo Starting TTS API server (CPU-only - Piper)...
@REM cd /d "%~dp0backend_cpu"
@REM python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload



@REM @echo off
@REM echo Starting TTS API server (CPU-only - Piper)...
@REM cd /d "%~dp0backend_cpu"
@REM "C:\Users\hophu\AppData\Local\Programs\Python\Python312\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
@REM pause




@echo off
echo Starting TTS API server (CPU-only - Piper)...
cd /d "%~dp0backend_cpu"
set PATH=%PATH%;C:\Users\hophu\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin
"C:\Users\hophu\AppData\Local\Programs\Python\Python312\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause