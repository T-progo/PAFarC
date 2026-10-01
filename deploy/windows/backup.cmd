@echo off
rem Consistent backup of the PRODUCTION database into C:\PharmaTech\backups (safe while running).
rem Keep copies of the encryption keys (C:\PharmaTech\config\.env) SEPARATELY and securely.
setlocal
cd /d C:\PharmaTech\config
set "PYTHONPATH=D:\Darlan - PAFarC"
"C:\PharmaTech\venv\Scripts\python.exe" -m scripts.backup_database C:\PharmaTech\backups
