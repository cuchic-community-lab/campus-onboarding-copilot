@echo off
setlocal
title XiaohaiGPT Backend

REM ============================================================
REM  XiaohaiGPT 一键启动（Windows）
REM  1) 检查 Python 与可选解析器  2) 增量建索引（缺文件降级）
REM  3) 启动服务 http://127.0.0.1:8000
REM ============================================================

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未找到 python，请先安装 Python 3.9+ 并加入 PATH。
    pause
    exit /b 1
)

set PYTHONPATH=%~dp0src;%PYTHONPATH%

echo [1/3] 检查可选解析器（pypdf / python-docx / openpyxl）...
python -c "import pypdf, docx, openpyxl" >nul 2>nul
if errorlevel 1 (
    echo   未检测到全部解析器，尝试安装（失败不阻塞）...
    python -m pip install --quiet pypdf python-docx openpyxl
)
python -c "import pypdf, docx, openpyxl" >nul 2>nul
if errorlevel 1 (
    echo   [WARN] 部分解析器不可用：PDF/Word/Excel 将降级为 metadata_only（不阻塞启动）。
) else (
    echo   [OK] 解析器就绪。
)

echo [2/3] 增量建索引（缺文件不报错，自动降级 metadata_only）...
python -m campus_copilot.cli build
if errorlevel 1 (
    echo   [ERROR] 索引构建失败，请检查站点根目录是否包含 files.json。
    pause
    exit /b 1
)

echo [3/3] 启动服务 http://127.0.0.1:8000
echo   按 Ctrl+C 停止。
python -m campus_copilot.cli serve --host 127.0.0.1 --port 8000

pause
