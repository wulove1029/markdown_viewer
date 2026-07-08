# -*- coding: utf-8 -*-
"""Per-project AI collab board launcher（薄殼）。

本檔只做兩件事：記住此專案的 PORT、找到中央工具路徑，然後把啟動工作
轉交給中央的 project_board_launcher.py——**啟動邏輯以後只改中央那份**，
本檔不需要跟著更新。

用法：
  1. 把本檔複製到專案的 AI協作 資料夾，改名 board_launcher.py
     （「＋新增專案」按鈕會自動做）。
  2. 改 PORT 為此專案專屬 port
     （8765 = 中央工具的板、8766 已被 coil_results_web 佔用，專案用 8767、8768…）。
     TOOL_DIR 通常留空 —— 會自動讀 ~/.ai_collab_tool_dir（由中央 repo 的
     檢查AI環境.bat 寫入）；只有想固定指到別的中央 repo 時才填。
  3. 用同層的啟動 .bat 雙擊執行（或直接 `py -3 -X utf8 board_launcher.py`）。

中文路徑一律放在本檔（UTF-8），不要放進 .bat —— cmd/PowerShell 的
codepage 轉換會把中文路徑弄成亂碼。
"""
TOOL_DIR = r""  # 留空 = 自動；要覆寫才填，例如 r"E:\AI協作"
PORT = 8772

import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


def _resolve_tool_dir():
    """TOOL_DIR 覆寫 > 環境變數 AI_COLLAB_TOOL_DIR > ~/.ai_collab_tool_dir"""
    if TOOL_DIR:
        return Path(TOOL_DIR), "TOOL_DIR"
    env = os.environ.get("AI_COLLAB_TOOL_DIR", "").strip()
    if env:
        return Path(env), "環境變數 AI_COLLAB_TOOL_DIR"
    cfg = Path.home() / ".ai_collab_tool_dir"
    if cfg.exists():
        return Path(cfg.read_text(encoding="utf-8").strip()), str(cfg)
    return None, None


def _ask_user_for_tool_dir(reason):
    """路徑錯誤時跳出資料夾選擇視窗讓使用者重設；回傳有效路徑或 None（取消）。"""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except Exception:
        print(reason)
        print("（無法開啟圖形視窗——請到中央 repo 重跑一次 檢查AI環境.bat）")
        return None
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    messagebox.showwarning(
        "AI 協作討論版",
        reason + "\n\n請在下一個視窗選擇中央工具資料夾\n（裡面要有 ai_collab_board.py，通常是 E:\\AI協作）。",
        parent=root)
    try:
        while True:
            chosen = filedialog.askdirectory(title="選擇中央 AI協作 工具資料夾", parent=root)
            if not chosen:
                return None
            chosen_dir = Path(chosen)
            if (chosen_dir / "ai_collab_board.py").exists():
                return chosen_dir
            messagebox.showerror("AI 協作討論版",
                                 "這個資料夾裡沒有 ai_collab_board.py，請重選。", parent=root)
    finally:
        root.destroy()


tool_dir, source = _resolve_tool_dir()
if tool_dir is None or not (tool_dir / "ai_collab_board.py").exists():
    reason = ("找不到中央工具路徑（尚未註冊）。" if tool_dir is None else
              "中央工具路徑失效：{}\n（來源：{}，可能是中央 repo 搬家了）".format(tool_dir, source))
    tool_dir = _ask_user_for_tool_dir(reason)
    if tool_dir is None:
        print("已取消。請到中央 repo 雙擊一次 檢查AI環境.bat 重新註冊路徑。")
        sys.exit(1)
    (Path.home() / ".ai_collab_tool_dir").write_text(str(tool_dir), encoding="utf-8")
    print("已儲存中央工具路徑：{}".format(tool_dir))
    if TOOL_DIR:
        print("注意：本檔開頭的 TOOL_DIR 覆寫仍指向舊路徑，請改掉或清空，否則下次還是會問。")

launch = tool_dir / "project_board_launcher.py"
if not launch.exists():
    print("中央 repo 缺 {}（版本太舊？請更新中央 repo）".format(launch))
    sys.exit(1)
sys.exit(subprocess.run(
    [sys.executable, "-X", "utf8", str(launch),
     "--doc-dir", str(PROJECT_DIR), "--port", str(PORT)] + sys.argv[1:]).returncode)
