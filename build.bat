@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   Finder 打包脚本
echo ========================================
echo.

:: 1. 确保 PyInstaller 已安装
.venv\Scripts\python.exe -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [*] 安装 PyInstaller ...
    .venv\Scripts\pip.exe install pyinstaller
)

:: 2. 检查 UPX（可选压缩工具）
::    把 upx.exe 放到本目录或 .venv\Scripts\ 即可自动检测
set "UPX_FOUND="
if exist "%~dp0upx.exe" (
    set "UPX_FOUND=1"
    set "PATH=%~dp0;%PATH%"
)
if exist "%~dp0.venv\Scripts\upx.exe" (
    set "UPX_FOUND=1"
)
if not defined UPX_FOUND (
    where upx >nul 2>&1 && set "UPX_FOUND=1"
)
if defined UPX_FOUND (
    echo [✓] UPX 已就绪
) else (
    echo [!] 未检测到 UPX，跳过压缩（不影响打包）
    echo     把 upx.exe 放到本目录即可启用压缩
)

:: 3. 清理旧构建
if exist build rmdir /s /q build
if exist dist\Finder rmdir /s /q dist\Finder

:: 4. 打包
echo.
echo [*] 开始打包（onedir 模式，预计 2-5 分钟）...
.venv\Scripts\python.exe -m PyInstaller macos_explorer.spec --clean --noconfirm

if errorlevel 1 (
    echo [✗] 打包失败！
    pause
    exit /b 1
)

:: 5. 复制 ICO 文件到输出目录
echo.
echo [*] 复制 ICO 文件 ...
if exist win.ico    copy /y win.ico    dist\Finder\ >nul
if exist winout.ico copy /y winout.ico dist\Finder\ >nul

:: 6. 计算大小
echo.
echo ========================================
echo   打包完成！
echo   输出目录: dist\Finder\
echo ========================================
echo   启动文件: dist\Finder\Finder.exe
echo.
for /f "usebackq tokens=2 delims= " %%a in (`powershell -NoProfile -Command "(Get-ChildItem -Recurse 'dist\Finder' | Measure-Object -Property Length -Sum).Sum / 1MB"`) do echo   总大小:   %%a MB
echo.
echo   提示: 将 win.ico / winout.ico 放在 Finder.exe 同目录即可加载图标
echo.
pause
