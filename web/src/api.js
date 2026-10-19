// 与本地 Python 服务通信。生成和运行是逐行 JSON 的流，边到边处理。

export async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} 返回 ${r.status}`);
  return r.json();
}

export async function postJSON(url, body, signal) {
  return sendJSON("POST", url, body, { signal });
}

// method：POST、PUT、DELETE；keepalive：页面关闭时也把请求发完（离开前存项目）
export async function sendJSON(method, url, body, { signal, keepalive } = {}) {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body), signal, keepalive });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.detail?.errors?.[0] || data.detail || `请求失败（${r.status}）`), { data, status: r.status });
  return data;
}

export async function stream(url, body, onEvent, signal) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new Error(data.detail || `请求失败（${r.status}）`);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i);
      buf = buf.slice(i + 1);
      if (line.trim()) onEvent(JSON.parse(line));
    }
  }
}
