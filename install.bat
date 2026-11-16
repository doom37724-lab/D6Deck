@echo off
rem D6Deck setup: installs the Python packages, then puts D6Deck icons on your Desktop.
cd /d "%~dp0"
where py >nul 2>nul || (
  echo Python is not installed. Get it from https://www.python.org/downloads/ ^(tick "Add to PATH"^), then run this again.
  pause
  exit /b 1
)
py -3 -m pip install --upgrade -r requirements.txt || (pause & exit /b 1)
py -3 d6deck.py shortcuts
echo.
echo Done! Plug in your D6 and open "D6Deck" on your Desktop.
pause
