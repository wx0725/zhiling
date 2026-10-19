"""模块进程的启动与崩溃（代码审查报告 2026-10-16 复检时发现）：
1. 以前用 multiprocessing 的 spawn 启动子进程，子进程会重新导入调用方的主脚本；
   没有 if __name__ == "__main__" 保护的脚本里调用引擎，子进程起不来，模块一直等到超时（5 秒、60 秒），还报成“超时”。
2. 子进程中途退出时，也要马上报告，而不是等到超时。"""
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from core import worker
from core.registry import ROOT


class UnguardedScript(unittest.TestCase):
    def test_engine_works_from_script_without_main_guard(self):
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(ROOT)!r})
            from core.engine import Engine
            g = {{"spec": "0.2", "id": "t", "name": "t", "intent": "", "inputs": {{}}, "outputs": {{"y": {{"type": "string"}}}},
                 "nodes": [{{"id": "f", "kind": "module", "module": "text.format@0", "config": {{"template": "hi"}}}}],
                 "edges": [{{"from": "f.text", "to": "$out.y"}}]}}
            r = Engine().run(g, {{}})
            print("RESULT", r.status, r.outputs.get("y"), (r.error or {{}}).get("code"))
        """)
        with tempfile.TemporaryDirectory() as d:  # 放在项目外，模拟使用者随手写的脚本
            p = Path(d) / "unguarded.py"
            p.write_text(script, encoding="utf-8")
            t = time.monotonic()
            out = subprocess.run([sys.executable, "-B", str(p)], capture_output=True, text=True, timeout=60, cwd=d,
                                 encoding="utf-8", errors="replace")
            took = time.monotonic() - t
        self.assertIn("RESULT completed hi None", out.stdout, out.stderr[-500:])
        self.assertLess(took, 4.5)  # 修改前要等满 text.format 的 5 秒超时


class CrashIsReportedAtOnce(unittest.TestCase):
    def tearDown(self):
        worker.shutdown()

    def test_module_killing_its_process(self):
        t = time.monotonic()
        with self.assertRaises(worker.WorkerCrash):
            worker.call("tests.fixtures.exit_module.module", "run", {}, 30)
        self.assertLess(time.monotonic() - t, 5)  # 不等 30 秒超时

    def test_next_call_still_works(self):
        with self.assertRaises(worker.WorkerCrash):
            worker.call("tests.fixtures.exit_module.module", "run", {}, 30)
        out = worker.call("modules.text_format.module", "run", {"template": "ok", "value": None}, 10)
        self.assertEqual(out, {"text": "ok"})


if __name__ == "__main__":
    unittest.main()
