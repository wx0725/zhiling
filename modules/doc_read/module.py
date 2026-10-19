import zipfile
import xml.etree.ElementTree as ET

from core.docsafe import check_no_doctype, check_zip
from core.errors import ModuleError
from core.paths import safe_path
from core.registry import ROOT

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _para(p) -> str:
    """一个段落的文字：只看文字块 <w:r> 里的内容（段落格式里的制表位定义不算）。
    <w:t> 是文字，<w:tab> 是制表符，<w:br>/<w:cr> 是段内换行；删除修订 <w:delText> 不读。"""
    out = []
    for r in p.iter(_W + "r"):
        for el in r:
            tag = el.tag
            if tag == _W + "t":
                out.append(el.text or "")
            elif tag == _W + "tab":
                out.append("\t")
            elif tag in (_W + "br", _W + "cr"):
                out.append("\n")
            elif tag == _W + "noBreakHyphen":
                out.append("-")
    return "".join(out)


def _cell(tc) -> str:
    """一个单元格的文字：里面的多个段落（以及表格里套的表格）拍平成一行，用空格隔开。"""
    parts: list[str] = []
    _blocks(tc, parts)
    return " ".join(x for x in (s.replace("\n", " ").replace("\t", " ").strip() for s in parts) if x)


def _blocks(parent, lines: list[str]):
    for child in parent:
        if child.tag == _W + "p":
            lines.append(_para(child))
        elif child.tag == _W + "tbl":
            for tr in [r for r in child if r.tag == _W + "tr"]:
                lines.append("\t".join(_cell(tc) for tc in tr if tc.tag == _W + "tc"))
        elif child.tag in (_W + "sdt", _W + "sdtContent", _W + "customXml", _W + "smartTag"):
            _blocks(child, lines)  # 内容控件等包装：读里面的段落


def run(inputs: dict) -> dict:
    p = safe_path(inputs["path"])
    if not p.is_absolute():
        p = ROOT / p
    ext = p.suffix.lower()
    if ext != ".docx":
        hint = {".doc": "；旧版 .doc 请先在 Word 里另存为 .docx", ".pdf": "；不支持 PDF"}.get(ext, "")
        raise ModuleError("unsupported_type", f"只能读 Word 的 .docx 文件，收到 {ext or '没有扩展名'}{hint}")
    if not p.is_file():
        raise ModuleError("not_found", f"找不到文件：{p}")
    try:
        with zipfile.ZipFile(p) as z:
            check_zip(z)
            data = z.read("word/document.xml")
            check_no_doctype(data)
            root = ET.fromstring(data)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as e:
        raise ModuleError("bad_file", f"{p.name} 不是有效的 Word 文件（可能已损坏或被加密）：{e}") from None
    body = root.find(_W + "body")
    lines: list[str] = []
    if body is not None:
        _blocks(body, lines)
    text = "\n".join(lines).strip("\n")
    return {"text": text[:max(inputs["max_chars"], 0)], "name": p.name}
