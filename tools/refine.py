"""帮使用者理清需求：顺着第一句模糊的想法不断追问，直到标准需求单（见 tools/spec.py）没有缺项。

模型只做一件事：把使用者的话（想法、自己写的回答、改过的卡片）读成需求单，没说清的地方留空，不猜。
缺哪一栏、问什么、先问哪个、选项点了怎么改单子、最后的需求文字，都由代码决定。
使用者只点了代码给的选项时，这一轮完全不调用模型。
"""
import json

from core.registry import load_registry
from tools import protocol, spec as sp
from tools.generate import DEFAULT_MODEL, chat, module_catalog, resolve

CARD_KEYS = ("given", "wanted", "steps", "cases")  # 旧界面的四栏，仍随结果返回

PROMPT = """你把使用者的想法读成一张标准需求单。工作流只能用下面这些模块，以及条件分支、合并、逐项循环。

你只负责理解使用者说了什么，不替他做决定：
- 使用者没有说清的栏目写 null（inputs、outputs、steps），或把 type、else 写成 null。不要猜，不要补他没提过的要求。
- 使用者明确说了不需要输入（例如要处理的网址已经写在需求里）时，inputs 写 []。
- inputs / outputs：[{"name": 小写英文加下划线, "type": 类型, "desc": 中文说明}]。类型只能是 string number integer boolean object file any 或 list<类型>；
  拿不准是“一个”还是“一组”时 type 写 null。desc 只写使用者说过的，不要添加他没提的内容（例如在结果里额外带上网址）；
  结果是文字就用 string 或 list<string>，除非使用者明确要带字段的数据，不要用 object。
- steps：[{"do": "这一步做什么", "module": "模块id@0 或 null", "each": 是否对列表逐项做}]，按顺序，尽量对应到模块。
- branches：使用者提到的条件 [{"when": "条件", "then": "成立时", "else": "不成立时，没说就写 null"}]。
- failures：使用者说了某一步失败（如网页打不开、文件读不到）时怎么办 [{"module": "模块id@0", "handle": "怎么办"}]，没说就不写。
- examples：使用者给的例子原文，没有就写 []。
- unsupported：现有模块做不到的部分（例如发邮件、发消息、读 PDF 或 Word、定时运行），要仔细对照模块说明里写明的“不能做什么”；这些部分不要写进 steps。
- options：对每个写成 null 的栏目，给 2 到 3 个使用者最可能的回答，键用栏目路径，例如 "inputs"、"inputs.0.type"、"outputs"、"steps"、"branches.0.else"。
- 语气、风格、措辞这类细节直接写进相应步骤的 do 里，不算缺项。

如果给了之前的需求单和回答：回答就是使用者的决定，必须按回答改，冲突的内容删掉或改掉。
如果给了使用者改过的卡片：以卡片为准。

只输出 JSON：
{"goal": "一句话目标", "inputs": [...] 或 null, "outputs": [...] 或 null, "steps": [...] 或 null,
 "branches": [], "failures": [], "examples": [], "unsupported": [], "options": {}}

可用模块：
{catalog}"""


class RefineError(Exception):
    pass


def extract(idea: str, answers: list, card: dict | None, prev: dict | None, model: str, url: str | None, reg) -> tuple[dict, int]:
    """调模型把使用者的话读成需求单（唯一需要理解语义的一步）"""
    user = [f"使用者的想法：{idea.strip()}"]
    if prev:
        user.append("之前的需求单：\n" + sp.to_json(prev))
    if answers:
        user.append("使用者的回答：\n" + "\n".join(f"- {a.get('ask', '')}：{a.get('answer', '')}" for a in answers))
    if card:
        user.append("使用者改过的需求卡片：\n" + json.dumps(card, ensure_ascii=False))
    req = protocol.Request("refine", PROMPT.replace("{catalog}", module_catalog(reg)), "\n\n".join(user),
                           temperature=0.2, check=sp.shape_error)
    reply = protocol.call(model, req, url, send_fn=lambda *a: chat(*a))
    if not reply.ok:
        raise RefineError("模型没有给出可用的需求单，请再试一次，或直接生成")
    return sp.normalize(reply.data, set(reg)), reply.calls


def result(idea: str, spec: dict, reg, calls: int) -> dict:
    spec["idea"] = idea.strip()  # 需求单只对应这句想法：想法改了，旧单子就不再用
    qs = sp.questions(spec, reg)
    return {"spec": spec, "card": sp.card(spec, reg), "questions": qs, "checklist": sp.checklist(spec, reg),
            "unsupported": list(spec["unsupported"]), "requirement": sp.requirement(idea, spec, reg),
            "complete": not qs and not sp.blocked(spec), "calls": calls}


def refine(idea: str, answers: list[dict] | None = None, card: dict | None = None, spec: dict | None = None,
           model: str = DEFAULT_MODEL, url: str | None = None) -> dict:
    """idea：最初的想法；answers：本轮及之前的回答 [{slot, ask, answer}]；card：使用者改过的卡片（只在改过时传）；
    spec：上一轮返回的需求单（界面原样送回）。"""
    reg = load_registry()
    prev = sp.clean_spec(spec, set(reg))
    if prev is not None and prev["idea"] != idea.strip():
        prev, answers, card = None, [], None  # 想法改过了：旧单子、旧问答、旧卡片都不再算数
    if prev is not None and card:
        prev["patches"] = {}  # 改过的卡片是使用者最新的决定（卡片本来就是按这些决定写的），不再用旧选项覆盖它
    answers = [a for a in answers or [] if isinstance(a, dict) and isinstance(a.get("answer"), str) and a["answer"].strip()]
    left = answers
    if prev is not None:
        new = [a for a in answers if a.get("slot") not in prev["answered"] or a.get("slot") == "unsupported"]
        prev, left = sp.apply_answers(prev, new, reg)
        if new and not left and not card:
            return result(idea, prev, reg, 0)  # 回答都由代码写进了单子：不调用模型
    model = resolve(model)
    data, calls = extract(idea, left, card, prev, model, url, reg)
    fresh = sp.carry(data, prev)
    asks = {g["ask"]: g["slot"] for g in sp.gaps({**fresh, "answered": {}}, reg)}
    for a in answers:  # 新单子也记下问过的：不再问（旧网页界面的回答没有栏目，按问题原文对上）
        slot = a.get("slot") if isinstance(a.get("slot"), str) else asks.get(a.get("ask"))
        if slot:
            fresh["answered"][slot] = a["answer"].strip()
    return result(idea, fresh, reg, calls)
