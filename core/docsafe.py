"""读取 Office 文档（docx/xlsx 都是 zip + XML）时的安全防护，doc.read 与 table.read 共用。

- check_no_doctype：解析前拒绝带 DOCTYPE / ENTITY 的 XML。xml.etree 会展开内部实体，
  构造嵌套实体可指数膨胀（billion laughs），撑爆内存或长时间卡死；普通文档从不带 DOCTYPE。
- check_zip：按 zip 成员声明的解压后大小预检（单个和总和都设上限），避免解压炸弹：
  一个高压缩比成员一次性读进内存会 OOM。使用者打开的“文档”可能来自不可信来源。
"""
import re
import zipfile

from core.errors import ModuleError

MEMBER_LIMIT = 200 * 1024 * 1024   # 单个成员解压后上限：约 200 MB
TOTAL_LIMIT = 500 * 1024 * 1024    # 全部成员解压后总和上限：约 500 MB

_DOCTYPE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.I)


def check_no_doctype(data: bytes) -> None:
    """XML 字节里含 DOCTYPE / ENTITY 就拒绝（在正文解析前调用）。
    同时检查去掉零字节后的副本：UTF-16 编码的 XML 在 ASCII 字符间夹零字节，
    直接用 ASCII 正则会漏过，但 ElementTree 按 BOM/声明仍能解析并展开实体。"""
    if _DOCTYPE.search(data) or _DOCTYPE.search(data.replace(b"\x00", b"")):
        raise ModuleError("bad_file", "这个文档里含有 XML 文档类型定义（DOCTYPE/ENTITY），出于安全不解析")


def check_zip(z: zipfile.ZipFile) -> None:
    """按 zip 目录里声明的解压后大小预检，超过上限就拒绝（在读取任何成员前调用）。"""
    total = 0
    for info in z.infolist():
        if info.file_size > MEMBER_LIMIT:
            raise ModuleError("bad_file",
                              f"文档里有过大的内部文件（{info.filename}，解压后约 {info.file_size >> 20} MB），出于安全不解析")
        total += info.file_size
    if total > TOTAL_LIMIT:
        raise ModuleError("bad_file", f"文档解压后总大小超过上限（约 {total >> 20} MB），出于安全不解析")
