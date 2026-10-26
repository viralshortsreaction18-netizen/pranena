@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-and-install.ps1" %*
