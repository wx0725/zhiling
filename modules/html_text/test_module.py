import json
import unittest
from pathlib import Path

from modules.html_text.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"max_chars": 50000, "include_title": True}


def text(html, **kw):
    return run({**DEFAULTS, "html": html, **kw})


class HtmlText(unittest.TestCase):
    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(run({**DEFAULTS, **ex["input"]}), ex["output"], ex["title"])

    def test_entities_and_whitespace(self):
        self.assertEqual(text("<p>a &amp; b&nbsp;&nbsp; c</p>\n\n  <p>  d  </p>")["text"], "a & b c\nd")

    def test_head_without_closing_tag_keeps_body(self):
        out = text("<head><title>T</title><body><p>正文</p>", include_title=False)
        self.assertEqual(out, {"text": "正文", "title": "T"})

    def test_title_is_first_line_by_default(self):
        """“网页内容里有没有某个词”只查 text：标题里的词也要能查到（example.com 正文里只有小写的 examples）。"""
        html = "<title>Example Domain</title><p>for documentation examples</p>"
        self.assertEqual(text(html)["text"], "Example Domain\nfor documentation examples")
        self.assertEqual(text("<title>只有标题</title>")["text"], "只有标题")

    def test_nested_and_unclosed_skips(self):
        self.assertEqual(text("<style>x</style>前<noscript><p>开启脚本</p></noscript>后")["text"], "前后")

    def test_block_tags_split_lines(self):
        self.assertEqual(text("<ul><li>一</li><li>二</li></ul>甲<br>乙")["text"], "一\n二\n甲\n乙")

    def test_svg_title_is_not_page_title(self):
        out = text("<title>Page</title><p>正文<svg><title>Search</title></svg></p>", include_title=False)
        self.assertEqual(out, {"text": "正文", "title": "Page"})

    def test_table_cells_stay_separate(self):
        html = "<table><tr><th>甲</th><th>乙</th></tr><tr><td>12</td><td>34</td></tr></table>"
        self.assertEqual(text(html)["text"], "甲 乙\n12 34")

    def test_max_chars_and_plain_text(self):
        self.assertEqual(text("abcdef", max_chars=3)["text"], "abc")
        self.assertEqual(text("")["text"], "")


if __name__ == "__main__":
    unittest.main()
