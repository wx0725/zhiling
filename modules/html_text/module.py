import re
from html.parser import HTMLParser

_SKIP = {"script", "style", "noscript", "template", "svg"}  # 不含 head：缺了 </head> 时会把整页正文吞掉
_BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "header", "footer", "nav",
          "aside", "main", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "hr", "dt", "dd", "form",
          "title", "body", "html"}
_CELL = {"td", "th"}  # 表格单元格：边界加空格，免得相邻两格的数字连成一个


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self.skip = 0          # 处在 script/style 等内部的层数
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title" and not self.skip:  # svg 里的 <title> 是图标提示，不是网页标题
            self.in_title = True
        elif tag in _SKIP:
            self.skip += 1
        self._boundary(tag)

    def _boundary(self, tag):
        if self.skip:
            return
        if tag in _BLOCK:
            self.parts.append("\n")
        elif tag in _CELL:
            self.parts.append(" ")

    def handle_startendtag(self, tag, attrs):
        self._boundary(tag)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag in _SKIP and self.skip:
            self.skip -= 1
        self._boundary(tag)

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title.append(data)
        else:
            self.parts.append(data)


def _clean(s: str) -> str:
    lines = (re.sub(r"[ \t\r\f\v ]+", " ", line).strip() for line in s.split("\n"))
    return "\n".join(line for line in lines if line)


def run(inputs: dict) -> dict:
    p = _Text()
    p.feed(inputs["html"])
    p.close()
    title = _clean(" ".join(p.title)).replace("\n", " ")
    lines = [title] if inputs["include_title"] and title else []
    lines.append(_clean("".join(p.parts)))
    text = "\n".join(x for x in lines if x)
    return {"text": text[: max(inputs["max_chars"], 0)], "title": title}
