"""织灵.exe：双击后用项目自己的 Python 环境（.venv）启动原生桌面版（python -m native），不弹命令行窗口，然后自己退出。
界面和后端代码都从项目目录读取，所以别处改了代码，重开窗口就是新的；exe 只需在换图标、换启动方式时重新打包：
  .venv\\Scripts\\python.exe -m PyInstaller native\\launcher.spec --noconfirm   （生成 native\\dist\\织灵.exe）
exe 放在项目目录里任意一层都行：从 exe 所在位置往上找含 native\\__main__.py 的目录。"""
import ctypes
import subprocess
import sys
from pathlib import Path

DETACHED = 0x0000_0008  # DETACHED_PROCESS：与本进程脱离，本进程退出后窗口照常运行


def find_root() -> Path | None:
    here = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    return next((p for p in here.parents if (p / "native" / "__main__.py").is_file()), None)


def main():
    root = find_root()
    pyw = root / ".venv" / "Scripts" / "pythonw.exe" if root else None
    if not pyw or not pyw.is_file():
        where = root or Path(sys.executable).parent
        ctypes.windll.user32.MessageBoxW(None, f"找不到项目的 Python 环境：\n{where}\\.venv\\Scripts\\pythonw.exe\n\n"
                                         "请把 织灵.exe 放在项目目录里，并按 README 建好 .venv。", "织灵", 0x10)
        return 1
    subprocess.Popen([str(pyw), "-B", "-m", "native"], cwd=root, creationflags=DETACHED, close_fds=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
