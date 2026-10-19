import http.client
import ipaddress
import json
import os
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

from core.errors import ModuleError
from core.registry import ROOT

USER_AGENT = "Mozilla/5.0 (workflow web.fetch/0.1)"
MAX_REDIRECTS = 5
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_BLOCK_MSG = ("出于安全不访问本机和内网地址（回环、链路本地、私有网段、共享地址段等）；"
              "确有需要时，在 model_settings.json 里设 allow_local_fetch: true，"
              "或设环境变量 WORKFLOW_ALLOW_LOCAL_FETCH=1")

# 每跳把已校验的 IP 固定给连接，杜绝 DNS 重绑定（校验时解析到公网 IP、连接时又解析到内网 IP 的 TOCTOU）。
# web.fetch 跑在独立的模块进程里、单线程顺序执行，用线程本地变量稳妥存放当前这一跳已校验的所有 IP。
_PIN = threading.local()


def _connect_pinned(conn) -> socket.socket:
    """连到本跳已校验的某个 IP（不再二次解析 DNS）；多个地址时依次回退，全失败抛最后一个错。
    _PIN.ips 为 None 时（放行本机/内网）按常规用主机名解析连接。"""
    targets = getattr(_PIN, "ips", None) or [conn.host]
    err: OSError | None = None
    for ip in targets:
        try:
            return socket.create_connection((ip, conn.port), conn.timeout, conn.source_address)
        except OSError as e:
            err = e
    raise err if err else OSError("没有可用的地址")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不自动跟随跳转：3xx 原样当作 HTTPError 返回，由 run() 逐跳校验后再手动跟随。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = _connect_pinned(self)  # Host 头仍用原域名
        if getattr(self, "_tunnel_host", None):
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = _connect_pinned(self)
        if getattr(self, "_tunnel_host", None):
            self.sock = sock
            self._tunnel()
            sock = self.sock
        server_hostname = self._tunnel_host or self.host  # 证书/SNI 仍按原域名校验
        self.sock = self._context.wrap_socket(sock, server_hostname=server_hostname)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


# 直连（ProxyHandler({})：不走系统/环境代理）。固定连接 IP 与代理不兼容，且会把 Proxy-Authorization
# 发到被连接的地址；本工具只做直连抓取，SSRF 固定 IP 才站得住。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect,
                                      _PinnedHTTPHandler, _PinnedHTTPSHandler)


def _allow_local() -> bool:
    """放行本机/内网的开关：环境变量 WORKFLOW_ALLOW_LOCAL_FETCH 为真，或 model_settings.json 的 allow_local_fetch 严格为 true。"""
    if os.environ.get("WORKFLOW_ALLOW_LOCAL_FETCH", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        s = json.loads((ROOT / "model_settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(s, dict) and s.get("allow_local_fetch") is True  # 只认布尔 true，"false"/"0" 不放行


def _blocked_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # 认不出的地址一律当作不安全
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped  # ::ffff:127.0.0.1 之类按其 IPv4 判断
    # 只允许全局可路由的公网地址：顺带拦下共享地址段(100.64/10,CGNAT)、保留、文档示例等非 is_private 的内网类地址
    return not addr.is_global or addr.is_multicast or addr.is_reserved or addr.is_unspecified


def _checked_ips(url: str) -> list[str]:
    """校验一跳的目标：必须是 http(s)，解析出的所有 IP 都不能是本机/内网地址；
    返回所有已校验的 IP（按解析顺序去重）用于固定连接，保留 IPv6→IPv4 之类的回退。"""
    if not url.startswith(("http://", "https://")):
        raise ModuleError("blocked_address", f"只能访问 http:// 或 https:// 地址：{url}")
    host = urllib.parse.urlsplit(url).hostname
    if not host:
        raise ModuleError("network", f"网址里没有主机名：{url}")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ModuleError("network", f"无法解析 {host}：{e}") from e
    ips: list[str] = []
    for info in infos:
        ip = info[4][0]
        if _blocked_ip(ip):
            raise ModuleError("blocked_address", _BLOCK_MSG)
        if ip not in ips:
            ips.append(ip)
    return ips


def _decode(raw: bytes, charset: str | None) -> str:
    for enc in (charset, "utf-8", "gb18030"):
        if enc:
            try:
                return raw.decode(enc)
            except (LookupError, UnicodeDecodeError):
                continue
    return raw.decode("utf-8", errors="replace")


def run(inputs: dict) -> dict:
    url = inputs["url"]
    if not url.startswith(("http://", "https://")):
        raise ModuleError("invalid_url", f"网址必须以 http:// 或 https:// 开头：{url}")
    allow = _allow_local()
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            _PIN.ips = None if allow else _checked_ips(current)  # 未放行：解析+校验一次，连接固定到这些 IP
            req = urllib.request.Request(current, headers={"User-Agent": USER_AGENT})
            try:
                resp = _OPENER.open(req, timeout=inputs["timeout_s"])
            except urllib.error.HTTPError as e:
                if e.code in _REDIRECT_CODES and e.headers.get("Location"):
                    nxt = urllib.parse.urljoin(current, e.headers["Location"])
                    e.close()
                    if not nxt.startswith(("http://", "https://")):
                        raise ModuleError("blocked_address", f"跳转到了不支持的地址：{nxt}") from None
                    current = nxt
                    continue
                resp = e  # 非跳转的非 2xx（如 404）：照常返回状态码
            except (socket.timeout, TimeoutError) as e:
                raise ModuleError("timeout", f"{inputs['timeout_s']} 秒内无响应：{current}") from e
            except urllib.error.URLError as e:
                if isinstance(e.reason, (socket.timeout, TimeoutError)):
                    raise ModuleError("timeout", f"{inputs['timeout_s']} 秒内无响应：{current}") from e
                raise ModuleError("network", f"无法访问 {current}：{e.reason}") from e
            with resp:
                try:  # 响应头到了、正文迟迟不来，也要按超时报告
                    raw = resp.read(inputs["max_bytes"])
                except (socket.timeout, TimeoutError) as e:
                    raise ModuleError("timeout", f"{inputs['timeout_s']} 秒内没有读完网页内容：{current}") from e
                except OSError as e:
                    raise ModuleError("network", f"读取 {current} 的内容时出错：{e}") from e
                charset = resp.headers.get_content_charset()
                return {"text": _decode(raw, charset), "status": resp.status, "url": resp.geturl()}
        raise ModuleError("too_many_redirects", f"跳转次数过多（超过 {MAX_REDIRECTS} 次）：{url}")
    finally:
        _PIN.ips = None
