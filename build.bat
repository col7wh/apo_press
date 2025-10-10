@echo off
echo Сборка diagnose.exe и main.exe...
pip install pyinstaller colorama pyserial

if exist dist rmdir /s /q dist
if exist build rmdir /s /q build


pyinstaller --onefile --console main.py

echo.
echo Готово! EXE-файлы в папке dist\
pause