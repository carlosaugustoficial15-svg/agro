@echo off
setlocal EnableExtensions
title Instalador do PASE Studio

rem Run the local installer when this file is inside a cloned repository.
if exist "%~dp0instalar_pase.ps1" (
    start "" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0instalar_pase.ps1"
    exit /b 0
)

rem This bootstrap can also be downloaded by itself from GitHub.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "$ErrorActionPreference='Stop'; try { $script=Join-Path $env:TEMP 'pase-installer.ps1'; Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/carlosaugustoficial15-svg/agro/main/instalar_pase.ps1' -OutFile $script; & $script } catch { Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show($_.Exception.Message, 'PASE - Instalador', 'OK', 'Error') | Out-Null; exit 1 }"
exit /b %errorlevel%
