import json
import os
import urllib.error
import urllib.request

from core.errors import ModuleError
from tools import llm

OLLAMA_URL = os.environ.get("WORKFLOW_OLLAMA_URL") or llm.default_ollama()


def run(inputs: dict) -> dict:
    # 没指定模型时：model_settings.json 的 think_model，或 Ollama 上装的第一个（见 tools/llm.py）
    model = inputs["model"].strip() or os.environ.get("WORKFLOW_THINK_MODEL", "").strip() or llm.think_model()
    if not model:
        raise ModuleError("model_not_found", "没有可用的本地模型：请在 Ollama 里下载一个模型，或在 model_settings.json 写 think_model")
    inputs = {**inputs, "model": model}
    text = inputs["text"][: inputs["max_chars"]]
    content = inputs["instruction"] if not text else f"{inputs['instruction']}\n\n---\n{text}"
    body = {
        "model": inputs["model"],
        "messages": [{"role": "user", "content": content}],
        "stream": False,
        "think": False,
        "options": {"temperature": inputs["temperature"]},
    }
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=170) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        if e.code == 404 and "not found" in detail:
            raise ModuleError("model_not_found", f"模型未安装：{inputs['model']}") from e
        raise ModuleError("service_unavailable", f"Ollama 返回 {e.code}：{detail[:200]}") from e
    except urllib.error.URLError as e:
        raise ModuleError("service_unavailable", f"无法连接 Ollama（{OLLAMA_URL}）：{e.reason}") from e
    answer = (data.get("message") or {}).get("content", "").strip()
    if not answer:
        raise ModuleError("empty_answer", "模型返回了空内容")
    return {"answer": answer}
