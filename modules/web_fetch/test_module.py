import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from core.errors import ModuleError
from modules.web_fetch.module import run


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302); self.send_header("Location", "/gbk"); self.end_headers(); return
        if self.path == "/gbk":
            body = "你好，示例".encode("gbk")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=gbk")
        elif self.path == "/ok":
            body = "<title>Example Domain</title>".encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        else:
            body = b"missing"
            self.send_response(404)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


class Fetch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # fixtures 用本机地址：显式放行，否则 SSRF 防护会拒绝 127.0.0.1
        cls._env = mock.patch.dict(os.environ, {"WORKFLOW_ALLOW_LOCAL_FETCH": "1"})
        cls._env.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._env.stop()

    def fetch(self, path, **kw):
        return run({"url": self.base + path, "timeout_s": 5, "max_bytes": 2_000_000, **kw})

    def test_ok(self):
        r = self.fetch("/ok")
        self.assertEqual((r["status"], r["text"]), (200, "<title>Example Domain</title>"))

    def test_404_is_not_error(self):
        self.assertEqual(self.fetch("/nope")["status"], 404)

    def test_redirect_and_charset(self):
        r = self.fetch("/redirect")
        self.assertEqual(r["text"], "你好，示例")
        self.assertTrue(r["url"].endswith("/gbk"))

    def test_max_bytes(self):
        self.assertEqual(len(self.fetch("/ok", max_bytes=7)["text"]), 7)

    def test_invalid_url(self):
        with self.assertRaises(ModuleError) as c:
            run({"url": "ftp://x", "timeout_s": 1, "max_bytes": 10})
        self.assertEqual(c.exception.code, "invalid_url")

    def test_network_error(self):
        with self.assertRaises(ModuleError) as c:
            run({"url": "http://127.0.0.1:1/", "timeout_s": 2, "max_bytes": 10})
        self.assertIn(c.exception.code, {"network", "timeout"})


class SSRF(unittest.TestCase):
    """默认（未放行）时拒绝本机 / 内网地址，且跳转的每一跳都校验。"""

    @classmethod
    def setUpClass(cls):
        cls._allow = mock.patch("modules.web_fetch.module._allow_local", return_value=False)
        cls._allow.start()

    @classmethod
    def tearDownClass(cls):
        cls._allow.stop()

    def _blocked(self, url):
        with self.assertRaises(ModuleError) as c:
            run({"url": url, "timeout_s": 2, "max_bytes": 10})
        self.assertEqual(c.exception.code, "blocked_address")

    def test_loopback_blocked(self):
        self._blocked("http://127.0.0.1:11434/")

    def test_localhost_name_blocked(self):
        self._blocked("http://localhost:8080/")

    def test_link_local_metadata_blocked(self):
        self._blocked("http://169.254.169.254/latest/meta-data/")

    def test_private_range_blocked(self):
        self._blocked("http://10.0.0.5/")
        self._blocked("http://192.168.1.1/")

    def test_shared_address_space_blocked(self):
        self._blocked("http://100.64.0.1/")   # CGNAT：既非 is_private 也非回环，但不可全局路由

    def test_connection_pinned_to_checked_ip(self):
        """连接固定到已校验的 IP（防 DNS 重绑定），Host 仍是原域名。"""
        import modules.web_fetch.module as m
        m._PIN.ips = ["203.0.113.7"]
        seen = {}
        try:
            with mock.patch.object(m.socket, "create_connection",
                                   side_effect=lambda addr, *a, **k: seen.setdefault("addr", addr)):
                m._PinnedHTTPConnection("example.com", 80).connect()
        finally:
            m._PIN.ips = None
        self.assertEqual(seen["addr"], ("203.0.113.7", 80))

    def test_connection_falls_back_to_next_ip(self):
        """多个已校验 IP：首个连不上时回退下一个，不重新解析。"""
        import modules.web_fetch.module as m
        m._PIN.ips = ["203.0.113.7", "198.51.100.9"]
        tried = []

        def fake(addr, *a, **k):
            tried.append(addr[0])
            if addr[0] == "203.0.113.7":
                raise OSError("down")
            return "sock"
        try:
            with mock.patch.object(m.socket, "create_connection", side_effect=fake):
                sock = m._connect_pinned(m._PinnedHTTPConnection("example.com", 80))
        finally:
            m._PIN.ips = None
        self.assertEqual((tried, sock), (["203.0.113.7", "198.51.100.9"], "sock"))

    def test_redirect_to_internal_blocked(self):
        """公网看似无害，但跳转到内网：逐跳校验拦下。"""
        class _Redir(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:11434/")
                self.end_headers()

            def log_message(self, *a):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Redir)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_port
        try:
            # 这一跳的起点（127.0.0.1）在未放行时本就会被拦；临时放行起点、仅验证跳转目标被拦
            with mock.patch("modules.web_fetch.module._checked_ips") as chk:
                seen = []

                def guard(url):
                    seen.append(url)
                    if "11434" in url:  # 跳转目标：内网，拦下
                        raise ModuleError("blocked_address", "x")
                    return ["127.0.0.1"]  # 起点放行并固定到本机（指向本测试的跳转服务器）
                chk.side_effect = guard
                with self.assertRaises(ModuleError) as c:
                    run({"url": f"http://127.0.0.1:{port}/", "timeout_s": 2, "max_bytes": 10})
                self.assertEqual(c.exception.code, "blocked_address")
                self.assertTrue(any("11434" in u for u in seen))  # 确实校验到了跳转后的地址
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
