import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

from core.docsafe import check_no_doctype, check_zip
from core.errors import ModuleError
from core.paths import safe_path
from core.registry import ROOT

TYPES = {".csv", ".tsv", ".xlsx", ".xlsm"}
_NUMBER = re.compile(r"^-?(0|[1-9]\d{0,14})(\.\d+)?$")  # 只认干净的数字；“007”“13800138000123456”这类保持文字
_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CELL = re.compile(r"^([A-Z]+)(\d+)$")


def _num(s: str):
    if _NUMBER.match(s):
        return float(s) if "." in s else int(s)
    return s


# ---------- CSV ----------

def _read_csv(p: Path, tsv: bool) -> list[list]:
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ModuleError("decode_error", f"{p.name} 既不是 UTF-8 也不是 GBK 编码")
    if tsv:
        delim = "\t"
    else:
        first = text.split("\n", 1)[0]
        delim = max(",\t;", key=first.count) if any(c in first for c in ",\t;") else ","
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delim))
    return [[_num(c.strip()) for c in r] for r in rows]


# ---------- XLSX ----------

def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _text_of(el) -> str:
    """共享字符串 / 内联字符串：直接的 <t>，或富文本里每段 <r><t> 拼起来（跳过拼音注音 rPh）。"""
    parts = []
    for child in el:
        if child.tag == _M + "t":
            parts.append(child.text or "")
        elif child.tag == _M + "r":
            parts += [t.text or "" for t in child.iter(_M + "t")]
    return "".join(parts)


def _xml(z: zipfile.ZipFile, name: str) -> bytes:
    """读一个 zip 成员的 XML 字节，并在解析前拒绝带 DOCTYPE/ENTITY 的内容（防实体膨胀）。"""
    data = z.read(name)
    check_no_doctype(data)
    return data


def _read_xlsx(p: Path, sheet: str) -> list[list]:
    try:
        z = zipfile.ZipFile(p)
    except (zipfile.BadZipFile, OSError) as e:
        raise ModuleError("bad_file", f"{p.name} 不是有效的 Excel 文件（可能已损坏或被加密）：{e}") from None
    with z:
        try:
            check_zip(z)
            wb = ET.fromstring(_xml(z, "xl/workbook.xml"))
            rels = ET.fromstring(_xml(z, "xl/_rels/workbook.xml.rels"))
            targets = {r.get("Id"): r.get("Target") for r in rels.iter(_PR + "Relationship")}
            sheets = [(s.get("name"), targets.get(s.get(_R + "id"))) for s in wb.iter(_M + "sheet")]
            if not sheets:
                raise ModuleError("bad_file", f"{p.name} 里没有工作表")
            if sheet:
                found = [t for n, t in sheets if n == sheet]
                if not found:
                    raise ModuleError("no_sheet", f"{p.name} 里没有叫“{sheet}”的工作表，有：{'、'.join(n for n, _ in sheets)}")
                target = found[0]
            else:
                target = sheets[0][1]
            target = target.lstrip("/") if target.startswith("/") else str(PurePosixPath("xl") / target)
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                shared = [_text_of(si) for si in ET.fromstring(_xml(z, "xl/sharedStrings.xml")).iter(_M + "si")]
            data = ET.fromstring(_xml(z, target))
        except ModuleError:
            raise
        except (KeyError, ET.ParseError, zipfile.BadZipFile, AttributeError, TypeError) as e:
            raise ModuleError("bad_file", f"{p.name} 的内容解析不了（可能已损坏）：{e}") from None
    rows: dict[int, dict[int, object]] = {}
    next_row = 0
    for r in data.iter(_M + "row"):
        ri = int(r.get("r")) - 1 if r.get("r") else next_row
        next_row = ri + 1
        cells, next_col = {}, 0
        for c in r.iter(_M + "c"):
            m = _CELL.match(c.get("r") or "")
            ci = _col_index(m.group(1)) if m else next_col
            next_col = ci + 1
            t, v = c.get("t", "n"), c.find(_M + "v")
            vt = v.text if v is not None and v.text is not None else None
            if t == "inlineStr":
                is_ = c.find(_M + "is")
                val = _text_of(is_) if is_ is not None else ""
            elif vt is None:
                val = ""
            elif t == "s":
                try:
                    val = shared[int(vt)]
                except (ValueError, IndexError):
                    val = ""
            elif t == "b":
                val = vt == "1"
            elif t in ("str", "e"):
                val = vt
            else:
                try:
                    f = float(vt)
                    val = int(f) if f.is_integer() and abs(f) < 1e15 else f
                except ValueError:
                    val = vt
            cells[ci] = val
        rows[ri] = cells
    if not rows:
        return []
    width = max((max(c) + 1 for c in rows.values() if c), default=0)
    return [[rows.get(i, {}).get(j, "") for j in range(width)] for i in range(max(rows) + 1)]


# ---------- 整理成行列表 ----------

def _headers(first: list, width: int) -> list[str]:
    out, seen = [], set()
    for j in range(width):
        h = first[j] if j < len(first) else ""
        h = ("是" if h else "否") if isinstance(h, bool) else str(h).strip()
        h = h or f"列{j + 1}"
        base, k = h, 2
        while h in seen:
            h, k = f"{base}_{k}", k + 1
        seen.add(h)
        out.append(h)
    return out


def run(inputs: dict) -> dict:
    p = safe_path(inputs["path"])
    if not p.is_absolute():
        p = ROOT / p
    ext = p.suffix.lower()
    if ext not in TYPES:
        hint = "；旧版 .xls 请先在 Excel 里另存为 .xlsx" if ext == ".xls" else ""
        raise ModuleError("unsupported_type", f"不支持 {ext or '没有扩展名'} 文件，只能读 .csv .tsv .xlsx{hint}")
    if not p.is_file():
        raise ModuleError("not_found", f"找不到文件：{p}")
    grid = _read_xlsx(p, inputs["sheet"].strip()) if ext in (".xlsx", ".xlsm") else _read_csv(p, ext == ".tsv")
    grid = [r for r in grid if any(c != "" for c in r)]  # 去掉整行都空的行
    if not grid:
        return {"rows": [], "headers": [], "count": 0}
    width = max(max((j + 1 for j, c in enumerate(r) if c != ""), default=0) for r in grid)  # 不算末尾的空列
    headers = _headers(grid[0], width)
    limit = max(inputs["max_rows"], 0)
    rows = [{h: (r[j] if j < len(r) else "") for j, h in enumerate(headers)} for r in grid[1:1 + limit]]
    return {"rows": rows, "headers": headers, "count": len(rows)}
