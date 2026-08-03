#!/usr/bin/env bash
# ============================================================
#  XiaohaiGPT 一键启动（Linux / macOS）
#  1) 检查 Python 与可选解析器  2) 增量建索引（缺文件降级）
#  3) 启动服务 http://127.0.0.1:8000
# ============================================================
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] 未找到 python3，请先安装 Python 3.9+。"
  exit 1
fi

export PYTHONPATH="$(pwd)/src:${PYTHONPATH:-}"

echo "[1/3] 检查可选解析器（pypdf / python-docx / openpyxl）..."
if ! python3 -c "import pypdf, docx, openpyxl" >/dev/null 2>&1; then
  echo "   未检测到全部解析器，尝试安装（失败不阻塞）..."
  python3 -m pip install --quiet pypdf python-docx openpyxl || true
fi
if python3 -c "import pypdf, docx, openpyxl" >/dev/null 2>&1; then
  echo "   [OK] 解析器就绪。"
else
  echo "   [WARN] 部分解析器不可用：PDF/Word/Excel 将降级为 metadata_only（不阻塞启动）。"
fi

echo "[2/3] 增量建索引（缺文件不报错，自动降级 metadata_only）..."
python3 -m campus_copilot.cli build
if [ $? -ne 0 ]; then
  echo "[ERROR] 索引构建失败，请检查站点根目录是否包含 files.json。"
  exit 1
fi

echo "[3/3] 启动服务 http://127.0.0.1:8000"
echo "   按 Ctrl+C 停止。"
exec python3 -m campus_copilot.cli serve --host 127.0.0.1 --port 8000
