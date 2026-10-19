# PyInstaller 配置：把 native/launcher.py 打包成带图标、不弹命令行窗口的 织灵.exe（只含启动器，不含 Qt 和后端）。
# 用法（在项目目录）：.venv\Scripts\python.exe -m PyInstaller native\launcher.spec --noconfirm
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis([os.path.join(SPECPATH, "launcher.py")], excludes=["PySide6", "tkinter", "unittest", "pydoc"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="织灵", console=False,
          icon=os.path.join(ROOT, "native", "assets", "icon.ico"), upx=False)
