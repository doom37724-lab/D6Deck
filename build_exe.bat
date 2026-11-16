@echo off
rem Builds dist\D6Deck.exe (one file, no Python needed to run it). Needs: py -m pip install pyinstaller
cd /d "%~dp0"
py -3 -m PyInstaller --noconfirm --onefile --windowed --name D6Deck --icon d6deck.ico ^
  --collect-all winrt --collect-all pycaw --collect-all psutil ^
  --hidden-import comtypes.stream --hidden-import d6ui ^
  --exclude-module setuptools --exclude-module comtypes.test d6deck.py
echo.
echo Done: dist\D6Deck.exe
pause
