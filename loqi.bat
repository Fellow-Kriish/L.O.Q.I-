@echo off
title L.O.Q.I. Voice Assistant

echo Select mode:
echo 1. Voice Mode (no wake word, press Enter to speak)
echo 2. Full Mode (wake word "Hey Loki" + voice)
echo 3. Text Mode (no mic, type commands)
echo.

set /p mode="Enter 1, 2, or 3 (default 1): "

if "%mode%"=="2" (
    echo Starting Full Mode...
    k:\code\L.O.Q.I\venv\Scripts\python.exe main.py
) else if "%mode%"=="3" (
    echo Starting Text Mode...
    k:\code\L.O.Q.I\venv\Scripts\python.exe main.py --text
) else (
    echo Starting Voice Mode...
    k:\code\L.O.Q.I\venv\Scripts\python.exe main.py --no-wake
)

pause
