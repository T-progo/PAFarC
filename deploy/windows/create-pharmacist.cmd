@echo off
rem Create a PharmaTech pharmacist in the PRODUCTION database (password is asked interactively).
rem Usage: C:\PharmaTech\create-pharmacist.cmd --full-name "Nome Completo" --crf "CRF-UF 00000" --login nome.sobrenome
setlocal
cd /d C:\PharmaTech\config
set "PYTHONPATH=D:\Darlan - PAFarC"
"C:\PharmaTech\venv\Scripts\python.exe" -m scripts.create_pharmacist %*
