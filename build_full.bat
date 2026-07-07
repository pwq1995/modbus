@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo ========================================
echo  Modbus GUI 完整打包工具 v3.0
echo ========================================
echo.

REM ===== 1. 检查Python =====
echo [1/6] 检查Python环境...
python --version > nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到Python！
    pause
    exit /b 1
)
echo  Python 环境正常

REM ===== 2. 检查虚拟环境 =====
echo [2/6] 检查虚拟环境...
if not exist ".venv" (
    echo 创建虚拟环境...
    python -m venv .venv
)
call .venv\Scripts\activate
echo  虚拟环境已激活

REM ===== 3. 安装依赖 =====
echo [3/6] 安装依赖...
pip install --upgrade pip -q
pip install pyinstaller pyside6 pandas openpyxl pyserial crcmod -q
echo  依赖安装完成

REM ===== 4. 下载UPX =====
echo [4/6] 检查UPX...
if not exist "upx.exe" (
    echo  下载UPX...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/upx/upx/releases/download/v4.2.4/upx-4.2.4-win64.zip' -OutFile 'upx.zip'"
    powershell -Command "Expand-Archive -Path 'upx.zip' -DestinationPath '.'"
    copy upx-4.2.4-win64\upx.exe upx.exe > nul
    rmdir /s /q upx-4.2.4-win64
    del upx.zip
    echo  UPX下载完成
) else (
    echo  UPX已存在
)

REM ===== 5. 清理旧文件 =====
echo [5/6] 清理旧文件...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist *.spec del /q *.spec
echo  清理完成

REM ===== 6. 执行打包 =====
echo [6/6] 开始打包...
echo.

pyinstaller --onefile --windowed ^
    --upx-dir "." ^
    --exclude-module matplotlib ^
    --exclude-module scipy ^
    --exclude-module IPython ^
    --exclude-module jupyter ^
    --exclude-module tensorflow ^
    --exclude-module torch ^
    --exclude-module keras ^
    --exclude-module PIL ^
    --exclude-module cv2 ^
    --exclude-module PyQt5 ^
    --exclude-module PyQt6 ^
    --exclude-module pytest ^
    --exclude-module setuptools ^
    --exclude-module tkinter ^
    --add-data "config.ini;." ^
    --add-data "1.xlsx;." ^
    --collect-all PySide6 ^
    --hidden-import numpy ^
    --hidden-import pandas ^
    --hidden-import openpyxl ^
    --hidden-import serial ^
    --hidden-import crcmod ^
    gui_main.py

if errorlevel 1 (
    echo.
    echo ========================================
    echo  打包失败！
    echo ========================================
    pause
    exit /b 1
)

echo.
echo ========================================
echo  ✅ 打包成功！
echo ========================================
echo  文件位置: dist\gui_main.exe
echo  文件大小: 
for %%i in (dist\gui_main.exe) do echo    %%~zi 字节
echo.
echo  使用UPX压缩: 是
echo ========================================
pause