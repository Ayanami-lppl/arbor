@echo off
cd /d "%~dp0"
"C:\Users\ksl\AppData\Local\Programs\Python\Python314\python.exe" scan.py C:\ D:\ --exclude data
pause
