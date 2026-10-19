"""运行检查：用示例输入实际运行生成的图，再让审查模型阅读每个节点的输入输出，找出“结构合法但做错了”的问题。

结构校验只能保证“接得上”，发现不了“把网址当成网页内容交给总结节点”这类错误；
但一旦真的跑一遍，运行记录里就能看到总结节点收到的是一串网址。
"""
import json
import time

from core.engine import SKIPPED, Engine, WorkflowInvalid
from core.registry import load_registry
from core.validate import value_matches
from tools.generate import _wf_modules, chat, module_catalog
from tools import protocol
from tools.protocol import ContextExhausted

TRUNC = 160       # 运行记录里每个字符串最多显示多少字（审查只需看出“收到的是什么”，不必看全文）
LIST_HEAD = 5     # 运行记录里每个列表最多显示前几项

# 与图无关或无法确认的运行错误码，记为“无结论”，不逼着起草模型删改步骤：
# 通用的环境/未知码（超时、取消、模块崩溃、写操作没法演练、模块返回不对），
# 外加所有“获取”类模块（抓网页、读文件等向外部要东西的）声明的错误码——这类失败多半出在环境或模型编的示例上。
# 其余都算能归因于图：引擎的校验/类型/结构错，以及逻辑、处理、输出类模块的配置错（如 logic.compare 的 invalid_op），交回修图。
ENV_CODES = frozenset({"timeout", "cancelled", "module_crash", "not_confirmed", "bad_output"})


def environment_codes(reg) -> set[str]:
    codes = set(ENV_CODES)
    for spec in reg.values():
        if spec.data.get("category") == "获取":
            codes.update(e.get("code") for e in spec.data.get("errors", []) if isinstance(e, dict) and e.get("code"))
    return codes

INPUT_PROMPT = """为下面这个工作流编造 2 组用于测试的输入。
- 只能使用工作流声明的输入名，值的类型必须符合声明。
- 如果图里有条件分支，两组输入要分别走到两侧（例如一个能打开的网址和一个打不开的网址、一个及格分数和一个不及格分数）。
- 网址请使用真实可访问的地址。{hint}
只输出 JSON：{{"samples": [{{输入名: 值}}, {{输入名: 值}}]}}"""

JUDGE_PROMPT = """你是工作流审查员。下面给出用户需求、一张工作流图，以及用示例输入实际运行后每个节点收到的输入和产生的输出。
请根据运行记录判断这张图有没有做错，重点检查：
1. 每个节点收到的数据是不是它该处理的东西（例如“总结网页内容”的节点应收到网页正文，而不是网址本身）；
2. 条件判断是否检查了需求要求的条件，分支是否选对了一侧，被跳过的节点是否本来就该跳过；
3. 最终输出是否满足需求。
不算问题的情况：模型写的文字措辞不同；示例网页内容简单；打不开的网页返回的错误页面内容；写入文件等会改动电脑的模块在检查时只演练、不真的写（输出的路径就是将要保存的位置）。
只指出确定的、能通过修改连线或配置解决的错误；没有问题就返回 ok 为 true。
改法必须遵守下面的规则，只能用列出的模块和它们支持的参数值：
- 一个输入只能接一条线；要换数据来源，就把原来那条线的起点改掉，而不是再加一条。
- if 带 value 时，被选中的一侧传出的就是 value：要让分支后的节点拿到网页正文，应把 fetch.text 接到 branch.value。
- if 不带 value 时，then/else 只能接控制线（终点只写节点 id）。
- 类型必须一致：数字、真假要先经 text.format 才能交给只收文本的模块；列表要经 text.join 才能变成文本。
- 判断语言、判断含义等需要理解文字的条件，用 llm.think 让模型只输出一个固定词，再用 logic.compare 的 eq 比较。
只输出 JSON：{"ok": true 或 false, "problems": [{"node": "节点id", "problem": "问题", "fix": "怎么改（哪条连线、哪个配置）"}]}

可用模块：
{catalog}"""


def _short(v, cut: dict | None = None):
    """把运行记录里的长文本和长列表截短，便于塞进审查提示；cut 累计一共省略了多少字、多少列表项，
    好把“被裁剪了多少”写进提示和结果，不让审查误以为看全了。"""
    cut = cut if cut is not None else {}
    if v is SKIPPED:
        return "<跳过>"
    if isinstance(v, str):
        if len(v) > TRUNC:
            cut["chars"] = cut.get("chars", 0) + (len(v) - TRUNC)
            return v[:TRUNC] + f"…（截断，共 {len(v)} 字）"
        return v
    if isinstance(v, dict):
        return {k: _short(x, cut) for k, x in v.items()}
    if isinstance(v, list):
        out = [_short(x, cut) for x in v[:LIST_HEAD]]
        if len(v) > LIST_HEAD:
            cut["items"] = cut.get("items", 0) + (len(v) - LIST_HEAD)
            out.append(f"…（还有 {len(v) - LIST_HEAD} 项未显示，共 {len(v)} 项）")
        return out
    return v


def graph_summary(wf: dict) -> str:
    def lines(g, ind=""):
        out = []
        for n in g["nodes"]:
            cfg = f" 配置 {json.dumps(n['config'], ensure_ascii=False)}" if n.get("config") else ""
            out.append(f"{ind}- {n['id']}（{n['kind']}{' ' + n['module'] if n.get('module') else ''}）{cfg}")
            if isinstance(n.get("body"), dict):
                out += lines(n["body"], ind + "    ")
        out += [f"{ind}  连线 {e['from']} → {e['to']}" for e in g["edges"]]
        return out
    return "\n".join(lines(wf))


def sample_ok(decl: dict, s) -> bool:
    """示例输入与运行引擎同一契约：必填且没有默认值的要给，不能有未声明的，给了的要符合类型；可选的可以省略。"""
    if not isinstance(s, dict) or set(s) - set(decl):
        return False
    for k, p in decl.items():
        if k in s:
            if not value_matches(p["type"], s[k]):
                return False
        elif p.get("required", True) is not False and "default" not in p:
            return False
    return True


def propose_inputs(wf: dict, requirement: str, model: str, url: str | None, hint: str = "", should_stop=None) -> list[dict]:
    if not wf["inputs"]:
        return [{}]
    decl = json.dumps({k: p["type"] for k, p in wf["inputs"].items()}, ensure_ascii=False)
    messages = [{"role": "system", "content": INPUT_PROMPT.format(hint=hint)},
                {"role": "user", "content": f"需求：{requirement}\n输入声明：{decl}\n工作流：\n{graph_summary(wf)}"}]
    for _ in range(2):
        if should_stop and should_stop():  # 停止后不再发起新的模型请求
            return []
        with protocol.task_scope("inputs"):
            text, _ = chat(model, messages, "json", 0.2, url)
        if should_stop and should_stop():  # 迟到的结果不算成功
            return []
        try:
            samples = json.loads(text).get("samples", [])
        except (json.JSONDecodeError, AttributeError):
            continue
        if not isinstance(samples, list):  # 例如 null、1、true：当作不合格回答，再问一次
            continue
        ok = [s for s in samples if sample_ok(wf["inputs"], s)]
        if ok:
            return ok[:2]
    return []


def parse_verdict(text: str):
    """有效结论只有两种：{"ok": true}，或 {"ok": false, "problems": [至少一条]}。其他一律视为无效，返回 (None, [])。"""
    try:
        v = json.loads(text)
    except json.JSONDecodeError:
        return None, []
    if not isinstance(v, dict) or not isinstance(v.get("ok"), bool):
        return None, []
    if v["ok"]:
        return v, []  # 以结论为准；ok 为真时附带的建议不当作错误
    if not isinstance(v.get("problems"), list):
        return None, []
    found = [f"节点 {p.get('node')}：{p.get('problem')}" + (f"；改法：{p['fix']}" if p.get("fix") else "")
             for p in v["problems"] if isinstance(p, dict) and p.get("problem")]
    return (v, found) if found else (None, [])


def _has_ref(wf) -> bool:
    """图里是否有引用别的工作流文件的子流程（subflow/ref）：有的话这些模块无法就地收集，审查要退回完整目录"""
    nodes = wf.get("nodes") if isinstance(wf, dict) else None
    for n in nodes if isinstance(nodes, list) else []:
        if not isinstance(n, dict):
            continue
        if n.get("kind") == "subflow" or "ref" in n:
            return True
        if isinstance(n.get("body"), dict) and _has_ref(n["body"]):
            return True
    return False


def rehearsable(reg) -> set[str]:
    """会写文件、并提供了演练入口的模块：检查时改为演练，照常检查参数、算出结果，但不改动电脑上的任何东西。
    会问使用者的模块也算：检查时没人可问，按没有回答运行。"""
    return {key for key, spec in reg.items()
            if (spec.data["side_effects"] == "write" and "dry_run" in spec.data["runtime"]) or spec.data.get("interaction") == "ask"}


def make_checker(requirement: str, judge_model: str, judge_url: str | None = None, hint: str = "",
                 engine_kwargs: dict | None = None, should_stop=None):
    """返回给 generate(post_check=...) 用的检查函数。"""
    reg = load_registry()
    rehearse = rehearsable(reg)
    env_codes = environment_codes(reg)  # 哪些运行错误算环境/未知（记无结论），哪些算图错（交回修图）

    def check(wf: dict):
        """返回 (问题列表, 记录)。记录里的 status 只有三种：
        passed＝跑完了示例且审查明确说没问题；failed＝发现问题（问题列表非空）；
        inconclusive＝没能得出结论（编不出示例、审查回答不合格），不能当作通过。"""
        t0 = time.time()
        if should_stop and should_stop():
            return [], {"status": "inconclusive", "reason": "已停止"}
        try:
            return _check(wf, t0)
        except ContextExhausted as e:
            # 运行记录太长放不下：如实报无结论，别把超长提示发出去让模型崩，也不当通过
            return [], {"status": "inconclusive", "reason": str(e)}

    def _check(wf: dict, t0: float):
        samples = propose_inputs(wf, requirement, judge_model, judge_url, hint, should_stop)
        if should_stop and should_stop():
            return [], {"status": "inconclusive", "reason": "已停止"}
        if not samples:
            return [], {"status": "inconclusive", "reason": "没能编出符合输入类型的示例，无法实际运行"}
        graph_problems, unsure, runs, completed, cut = [], [], [], 0, {}
        # 检查时不真的写：有演练入口的写模块改为演练（仍在可停止的独立进程里），其余写操作一律拒绝
        engine = Engine(confirm=lambda spec, path, target=None: False, should_stop=should_stop, rehearse=rehearse,
                        **(engine_kwargs or {}))
        for s in samples:
            try:
                res = engine.run(wf, s)
            except WorkflowInvalid as e:  # 正常不会发生：调用前已校验
                return [f"图没有通过校验：{e.errors[0]}"], {"status": "failed", "samples": samples}
            if res.error and res.error.get("code") == "cancelled":
                return [], {"status": "inconclusive", "reason": "已停止", "samples": samples}
            # 直接用引擎的原始记录裁剪一次（不经 res.to_json 的 2000 字预览），裁剪量统计才准确
            runs.append({"inputs": _short(s, cut), "status": res.status, "error": res.error,
                         "outputs": _short(res.outputs, cut),
                         "records": [{"path": r.path, "status": r.status, "reason": r.reason,
                                      "inputs": _short(r.inputs, cut), "outputs": _short(r.outputs, cut)}
                                     for r in res.records]})
            if res.status == "failed":
                where = (f"示例输入 {json.dumps(s, ensure_ascii=False)}：节点 {res.error['path']}："
                         f"{res.error['code']}：{res.error['message']}")
                # 能归因于图的（校验/类型/结构错、模块配置错）才交回修图；抓取失败、超时、崩溃等与图无关或无法确认的记无结论
                (unsure if res.error["code"] in env_codes else graph_problems).append(where)
            else:
                completed += 1
        base = {"samples": samples, "runs": runs}
        if cut:  # 运行记录被截断了多少，所有分支（含失败/无结论）都带上，别让审查或结果当成看全了
            base["truncated"] = cut
        if graph_problems:
            return graph_problems, {**base, "status": "failed", "seconds": round(time.time() - t0, 1)}
        if not completed:
            # 一个样例都没真正跑通，没有可审查的运行记录：无结论，不当通过，也不逼着删步骤
            reason = ("实际运行都没能完成，且与图无关或无法确认：" + "；".join(unsure)) if unsure \
                else "没有样例跑通，无法实际运行验证"
            return [], {**base, "status": "inconclusive", "reason": reason, "seconds": round(time.time() - t0, 1)}
        if unsure:  # 部分样例因环境/未知原因没跑通：如实记下，不把局部结果当完整验证
            base = {**base, "inconclusive_runs": unsure}
        # 审查只需要图里实际用到的模块（压缩视图 + 这些模块的完整说明），不塞整份目录，省提示空间；
        # 图若引用了别的工作流文件（subflow/ref），这些模块无法就地收集，退回完整压缩目录以免审查看不懂
        used = set(_wf_modules(wf))
        catalog = module_catalog(reg) if _has_ref(wf) else module_catalog(reg, full=used, only=used)
        # 只把真正跑通的运行记录交给审查模型，避免它拿打不开的网页等环境问题当图错
        trace = json.dumps([r for r in runs if r["status"] == "completed"], ensure_ascii=False, indent=1)
        note = ""
        if cut:
            note = (f"\n\n（注意：运行记录里的长文本/长列表已截断，约省略 {cut.get('chars', 0)} 字、"
                    f"{cut.get('items', 0)} 个列表项；你看到的是片段，不代表全部，不要据此断定内容完整或缺失。）")
        messages = [{"role": "system", "content": JUDGE_PROMPT.replace("{catalog}", catalog)},
                    {"role": "user", "content": f"需求：{requirement}\n\n工作流：\n{graph_summary(wf)}\n\n运行记录：\n{trace}{note}"}]
        raws = []
        for _ in range(2):  # 审查回答不合格时再问一次
            if should_stop and should_stop():
                return [], {**base, "status": "inconclusive", "reason": "已停止"}
            with protocol.task_scope("judge"):
                text, secs = chat(judge_model, messages, "json", 0, judge_url)
            if should_stop and should_stop():  # 迟到的结果不算成功
                return [], {**base, "status": "inconclusive", "reason": "已停止"}
            raws.append(text)
            verdict, found = parse_verdict(text)
            if verdict is not None:
                status = "passed" if verdict["ok"] else "failed"
                out = {**base, "status": status, "verdict": verdict,
                       "seconds": round(time.time() - t0, 1), "judge_seconds": round(secs, 1)}
                if status == "passed" and unsure:
                    # 有样例因环境/未知原因没跑通：只审查了跑通的部分，不算完整验证，降级为无结论
                    out["status"] = "inconclusive"
                    out["reason"] = "只有部分样例跑通，审查未发现问题但不构成完整验证：" + "；".join(unsure)
                    return [], out
                return found, out
        return [], {**base, "status": "inconclusive", "reason": "审查模型两次都没有给出有效结论", "judge_raw": raws}

    return check
