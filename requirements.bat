@echo off
chcp 65001 >nul
echo ======================================
echo Python依赖安装程序
echo ======================================
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误]未检测到Python环境，请先安装Python并配置环境变量！
    pause
    exit /b 1
)
echo [正在批量安装所需依赖包...]
python -m pip install --upgrade pip
python -m pip install psutil pyside6 pywin32 pywinstyles winshell send2trash
echo.
echo [依赖安装完成]
echo.
start py macos_explorer_combined.pyw
exit