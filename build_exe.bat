@echo off
REM One-click build of RawToJpeg.exe (FFmpeg is downloaded and bundled automatically)
python -m pip install -r requirements.txt
if errorlevel 1 goto :err
python build_exe.py
if errorlevel 1 goto :err
echo.
echo Finished: dist\RawToJpeg.exe
pause
exit /b 0
:err
echo Build failed.
pause
exit /b 1
