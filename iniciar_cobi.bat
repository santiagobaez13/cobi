@echo off
rem Arranca a Cobi en su propia ventana, aparte de VS Code. Si cerrás esta ventana, Cobi se apaga
title Cobi
cd /d "%~dp0"
call venv\Scripts\activate.bat
python main.py
rem Si Cobi se cae, la ventana queda abierta para que se vea el error
pause
