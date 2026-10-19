"""在本程序里启动后端服务：先拉起 model_settings.json 里要求由本程序启动的 Ollama 服务（留在后台，见 tools/llm.py），
再在后台线程里跑网页服务的接口（server.app），只监听本机、端口每次随机挑一个空闲的，只给这个窗口用。
关闭窗口时服务随程序一起结束；Ollama 留在后台（与 启动网页.cmd 相同）。"""
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path



def project_root() -> Path:
    """项目目录：打包后是 exe 旁边往上找含 modules 的目录，开发时是本文件的上一层"""
    if getattr(sys, "frozen", False):
        for p in Path(sys.executable).resolve().parents:
            if (p / "modules").is_dir() and (p / "examples").is_dir():
                return p
    return Path(__file__).resolve().parent.parent


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def start_ollama() -> None:
    """设置里要求由本程序启动的 Ollama 服务（没有就什么都不做）"""
    try:
        from tools.llm import start_ollama_servers
        start_ollama_servers()
    except Exception:  # 启动不了也不影响窗口：生成时会说明没有可用的模型
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Backend:
    """后台线程里的接口服务。start() 返回后 base 可用；stop() 结束它"""

    def __init__(self):
        self.base = ""
        self.error = ""
        self._server = None
        self._thread = None

    def start(self, timeout: float = 60) -> bool:
        root = project_root()
        os.chdir(root)  # 模块、示例、保存的工作流都按项目目录找
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        log = root / "native" / "server.log"
        logging.basicConfig(filename=log, level=logging.WARNING, encoding="utf-8", force=True)
        threading.Thread(target=start_ollama, daemon=True).start()
        try:
            import importlib

            import uvicorn
            # 后端代码（server、core、tools、modules）始终从项目目录读取，不打包进 exe：别处改了后端，重开窗口就是新的
            app = importlib.import_module("server.app").app
        except Exception as e:  # 依赖缺失等：窗口里显示原因
            self.error = f"没能载入后端：{type(e).__name__}: {e}"
            return False
        port = _free_port()
        # 打包成窗口程序后没有控制台：不让 uvicorn 往标准输出写日志
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        self.base = f"http://127.0.0.1:{port}"
        started = time.monotonic()
        while not _http_ok(f"{self.base}/api/health"):  # 没配置模型时 /api/config 会报错，不能拿它判断是否就绪
            if not self._thread.is_alive():
                self.error = f"后端服务启动失败，详情见 {log}"
                return False
            if time.monotonic() - started > timeout:
                self.error = f"后端服务 {int(timeout)} 秒内没有就绪，详情见 {log}"
                return False
            time.sleep(0.1)
        return True

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)
