@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "Start-Process -FilePath 'C:\Users\ksl\AppData\Local\Programs\Python\Python314\python.exe' -ArgumentList 'scan.py C:\ D:\ --exclude data' -WorkingDirectory '%~dp0.' -Verb RunAs -Wait"
pause
