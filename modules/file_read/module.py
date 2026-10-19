import codecs

from core.errors import ModuleError
from core.paths import safe_path
from core.registry import ROOT

TEXT_TYPES = {".txt", ".md", ".csv", ".tsv", ".json", ".log", ".xml", ".html", ".htm", ".yaml", ".yml"}


def run(inputs: dict) -> dict:
    p = safe_path(inputs["path"])
    if not p.is_absolute():
        p = ROOT / p
    if p.suffix.lower() not in TEXT_TYPES:
        raise ModuleError("unsupported_type", f"不支持 {p.suffix or '没有扩展名'} 文件，只能读 {' '.join(sorted(TEXT_TYPES))}")
    if not p.is_file():
        raise ModuleError("not_found", f"找不到文件：{p}")
    limit = max(inputs["max_chars"], 0)
    want = limit * 4 + 4  # 一个字最多 4 个字节（另加 BOM）：只读够用的部分，大文件也不会整个读进内存
    with p.open("rb") as f:
        raw = f.read(want)
        whole = len(raw) < want or not f.read(1)
    for enc in ("utf-8-sig", "gbk"):
        try:
            # 增量解码：文件没读完时，末尾被截断的半个字不算错，留给下一段（这里直接丢弃）
            text = codecs.getincrementaldecoder(enc)().decode(raw, final=whole)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ModuleError("decode_error", f"{p.name} 既不是 UTF-8 也不是 GBK 编码")
    text = text.replace("\r\n", "\n")
    return {"text": text[:limit], "name": p.name}
