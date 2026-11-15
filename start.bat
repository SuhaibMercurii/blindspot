@echo off
rem Windows: double-click this file to start Blindspot. Needs Python 3.10 or newer from python.org.
rem This file was written on a Mac and has not been run on Windows.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo First start: setting up, about a minute, needs internet...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --quiet -r requirements.txt
)
if not exist .env echo No .env file here: saved runs replay with real data, new runs use made-up sample data.
echo Blindspot is starting at http://localhost:8000   (close this window to stop it)
start "" "http://localhost:8000/?replay=pinned&fast=1"
.venv\Scripts\python -m uvicorn app:app --port 8000
