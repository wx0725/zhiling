"""本地网页服务：给前端提供工作流列表、校验、一句话生成（流式进度）、运行（流式节点状态）。

只监听本机。生成与运行耗时较长，用逐行 JSON（NDJSON）流式返回进度。
"""
import asyncio
import contextlib
import json
import queue
import re
import secrets
import threading
import time
import urllib.request
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import tools.generate as gen
import tools.refine as refine_tool
from tools import keystore, llm, setup_ai
from core import types, worker
from core.engine import Engine, WorkflowInvalid
from core.paths import ref_within
from core.registry import ROOT, load_registry
from core.validate import validate_workflow
from tools.semantic import make_checker

EXAMPLES = ROOT / "examples"
SAVED = ROOT / "workflows"
WEB_DIST = ROOT / "web" / "dist"

@contextlib.asynccontextmanager
async def _lifespan(app):
    yield
    worker.shutdown()  # 服务关闭时结束所有空闲的模块进程


app = FastAPI(title="工作流", lifespan=_lifespan)


# ---------- 模型配置：生成和审查都用顶栏选的模型（见 tools/llm.py），没选过就用第一个能用的 ----------
def model_config(model: str | None = None) -> dict:
    """生成、理清需求、实跑审查用哪个模型：请求里指定了（项目自己选的）就用它，否则用设置里选的默认模型"""
    choice = model if isinstance(model, str) and llm.valid(model) else llm.chosen()
    if not choice:
        raise llm.LLMError("没有可用的模型：请安装 Ollama 并下载一个模型，或设置云端 API 的密钥，或登录订阅命令行")
    url = gen.OLLAMA  # 只对普通模型名有意义；ollama:// 写法自带地址
    return {"generator": choice, "generator_url": url, "judge": choice, "judge_url": url}


# ---------- 工作流文件 ----------
def _list_dir(d: Path, source: str) -> list[dict]:
    out = []
    for f in sorted(d.glob("*.workflow.json")):
        try:
            wf = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(wf, dict):  # 内容是 null、列表、数字等：跳过这一个，不影响其他文件
            continue
        text = lambda v, d="": v if isinstance(v, str) else d  # noqa: E731
        out.append({"file": f.name, "source": source, "id": text(wf.get("id")), "name": text(wf.get("name"), f.name),
                    "intent": text(wf.get("intent"))})
    return out


def _path(source: str, file: str) -> Path:
    if source not in ("examples", "saved") or not re.fullmatch(r"[\w\-]+\.workflow\.json", file):
        raise HTTPException(400, "文件名不合法")
    return (EXAMPLES if source == "examples" else SAVED) / file


@app.get("/api/health")
def health():
    """服务起来了就返回 ok（桌面窗口等它就绪；和有没有配置模型无关，没配置时也要能进引导）"""
    return {"ok": True}


@app.get("/api/config")
def get_config():
    return model_config()


@app.get("/api/models")
def get_models():
    """可选的模型（本地、云端 API、订阅命令行）以及当前选的是哪个"""
    return {"chosen": llm.chosen(), "choices": llm.choices()}


class ModelBody(BaseModel):
    model: str


@app.put("/api/models")
def set_model(body: ModelBody):
    if not llm.valid(body.model):
        raise HTTPException(400, "没有这个模型")
    llm.save_choice(body.model)
    return {"chosen": body.model, "choices": llm.choices()}


@app.get("/api/modules")
def get_modules():
    return [{"key": k, **s.data} for k, s in sorted(load_registry().items())]


@app.get("/api/workflows")
def list_workflows():
    SAVED.mkdir(exist_ok=True)
    return _list_dir(SAVED, "saved") + _list_dir(EXAMPLES, "examples")


@app.get("/api/workflows/{source}/{file}")
def get_workflow(source: str, file: str):
    p = _path(source, file)
    if not p.is_file():
        raise HTTPException(404, "没有这个工作流")
    return json.loads(p.read_text(encoding="utf-8"))


# 工作流来自哪里，决定子流程相对路径从哪个文件夹解析（与命令行 tools.run 用文件所在目录一致）
# new：在开始页“从空白开始”新建、还没保存的工作流
BASE_DIRS = {"examples": EXAMPLES, "saved": SAVED, "generated": SAVED, "draft": SAVED, "new": SAVED}


def _base(source: str) -> Path:
    if source not in BASE_DIRS:
        raise HTTPException(400, "source 只能是 examples、saved、generated、draft 或 new")
    return BASE_DIRS[source]


class WorkflowBody(BaseModel):
    workflow: dict
    source: str = "saved"
    overwrite: bool = False  # 保存：在画布上编辑了“我保存的”工作流，明确要求写回原文件


@app.post("/api/workflows")
def save_workflow(body: WorkflowBody):
    wf = body.workflow
    SAVED.mkdir(exist_ok=True)
    errors = validate_workflow(wf, load_registry(), SAVED)  # 保存后子流程引用相对“我保存的”文件夹解析
    if errors:
        raise HTTPException(422, {"errors": errors})
    if body.overwrite:
        file = f"{wf['id']}.workflow.json"
        (SAVED / file).write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"source": "saved", "file": file, "id": wf["id"], "renamed": False}
    # 不覆盖已有的同名工作流：内容相同就当作已保存，不同就换个名字另存（id 跟着改，文件名与 id 保持一致）
    base_id, n = wf["id"], 1
    while True:
        wid = base_id if n == 1 else f"{base_id}_{n}"
        file = f"{wid}.workflow.json"
        target = SAVED / file
        doc = wf if wid == base_id else {**wf, "id": wid}
        if not target.exists():
            target.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"source": "saved", "file": file, "id": wid, "renamed": wid != base_id}
        try:
            if json.loads(target.read_text(encoding="utf-8")) == doc:
                return {"source": "saved", "file": file, "id": wid, "renamed": wid != base_id}
        except (OSError, json.JSONDecodeError):
            pass
        n += 1


# ---------- 项目：左侧项目栏里的一个个工作区，各自记着正在做的工作流（含没存进“我保存的”的改动） ----------
PROJECTS = ROOT / "projects"
PROJECT_SOURCES = ("examples", "saved", "generated", "draft", "new")
# 项目文件的读、改、写、删整段加锁：两个请求同时改同一个项目（两个窗口、关页面时补发的保存）不会互相覆盖，
# 改名与自动保存不会丢掉一边，删掉的项目也不会被晚到的保存写回来
_PROJECT_LOCK = threading.Lock()


def _project_path(pid: str) -> Path:
    if not re.fullmatch(r"p_[0-9a-z]{6,40}", pid):
        raise HTTPException(400, "项目编号不合法")
    return PROJECTS / f"{pid}.project.json"


def _read_project(p: Path) -> dict | None:
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _write_project(p: Path, doc: dict) -> None:
    PROJECTS.mkdir(exist_ok=True)
    tmp = p.with_name(f"{p.name}.{secrets.token_hex(4)}.tmp")  # 先写临时文件再替换：写到一半断电也不会留下坏文件
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _now() -> str:
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"


def _project_meta(doc: dict) -> dict:
    return {k: doc.get(k) for k in ("id", "name", "created", "updated", "model")}


class ProjectBody(BaseModel):
    name: str | None = None
    model: str | None = None  # 这个项目生成、理清需求用哪个模型；空字符串表示跟着默认
    workflow: dict | None = None
    source: str | None = None
    file: str | None = None
    dirty: bool | None = None
    requirement: str | None = None
    clear: bool = False  # 清空项目里的工作流，回到开始页
    rev: int | None = None  # 网页端给每次保存编的号，只增不减：晚到的旧保存不会覆盖新内容


def _apply_project(doc: dict, body: ProjectBody) -> dict:
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "项目名不能为空")
        doc["name"] = name[:80]
    if body.source is not None and body.source not in PROJECT_SOURCES:
        raise HTTPException(400, "source 不合法")
    if body.file is not None and not re.fullmatch(r"[\w\-]+\.workflow\.json", body.file):
        raise HTTPException(400, "文件名不合法")
    if body.clear:
        doc.update(workflow=None, source=None, file=None, dirty=False)
    elif body.workflow is not None:
        doc.update(workflow=body.workflow, source=body.source or "new", file=body.file, dirty=bool(body.dirty))
    elif body.dirty is not None or body.source is not None:
        doc.update({k: v for k, v in (("dirty", body.dirty), ("source", body.source)) if v is not None})
    if body.requirement is not None:
        doc["requirement"] = body.requirement[:4000]
    if body.model is not None:
        if body.model and not llm.valid(body.model):
            raise HTTPException(400, "没有这个模型")
        doc["model"] = body.model or None
    if body.name is not None:
        doc["auto_name"] = False  # 使用者起过名字，之后不再跟着工作流改名
    elif doc.get("auto_name"):  # 还没起过名字：跟着里面的工作流（或生成时的需求）叫
        wf = doc.get("workflow")
        wf_name = wf.get("name") if isinstance(wf, dict) else None
        req = doc.get("requirement") if isinstance(doc.get("requirement"), str) else ""
        if isinstance(wf_name, str) and wf_name.strip():
            doc["name"] = wf_name.strip()[:80]
        elif req.strip():
            doc["name"] = req.strip()[:24]
    return doc


@app.get("/api/projects")
def list_projects():
    PROJECTS.mkdir(exist_ok=True)
    with _PROJECT_LOCK:
        docs = [d for d in (_read_project(p) for p in PROJECTS.glob("p_*.project.json")) if d and isinstance(d.get("id"), str)]
    return [_project_meta(d) for d in sorted(docs, key=lambda d: str(d.get("updated") or ""), reverse=True)]


@app.post("/api/projects")
def create_project(body: ProjectBody):
    now = _now()
    pid = f"p_{int(time.time() * 1000):x}{secrets.token_hex(3)}"
    doc = {"id": pid, "name": "未命名项目", "created": now, "updated": now,
           "workflow": None, "source": None, "file": None, "dirty": False, "requirement": "", "auto_name": True}
    _apply_project(doc, body)
    if body.rev is not None:
        doc["rev"] = body.rev
    with _PROJECT_LOCK:
        _write_project(_project_path(pid), doc)
    return doc


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = _project_path(pid)
    with _PROJECT_LOCK:
        doc = _read_project(p)
    if doc is None:
        raise HTTPException(404, "没有这个项目")
    return doc


@app.put("/api/projects/{pid}")
def update_project(pid: str, body: ProjectBody):
    p = _project_path(pid)
    with _PROJECT_LOCK:
        doc = _read_project(p)
        if doc is None:
            raise HTTPException(404, "没有这个项目")
        if body.rev is not None:
            stored = doc.get("rev")
            if isinstance(stored, int) and stored > body.rev:
                raise HTTPException(409, "项目里已经存着更新的内容")
            doc["rev"] = body.rev
        _apply_project(doc, body)
        doc["updated"] = _now()
        _write_project(p, doc)
    return _project_meta(doc)


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    p = _project_path(pid)
    with _PROJECT_LOCK:
        if not p.is_file():
            raise HTTPException(404, "没有这个项目")
        p.unlink()
    return {"ok": True}


@app.post("/api/validate")
def validate(body: WorkflowBody):
    return {"errors": validate_workflow(body.workflow, load_registry(), _base(body.source))}


@app.post("/api/ports")
def subflow_ports(body: WorkflowBody):
    """引用文件形式的子流程（ref）：返回每个被引用文件声明的输入输出，供画布画出插孔。"""
    base, out = _base(body.source), {}

    def walk(g: dict, d: Path):
        for n in g.get("nodes", []) if isinstance(g, dict) else []:
            if n.get("kind") == "subflow" and isinstance(n.get("ref"), str):
                p = (d / n["ref"]).resolve()
                if not ref_within(p, base) or n["ref"] in out:  # 只读项目和工作流所在文件夹里的文件（口径同引擎/校验器）
                    continue
                try:
                    sub = json.loads(p.read_text(encoding="utf-8"))
                    ins, bad_in = _port_decl(sub.get("inputs", {}), "inputs")
                    outs, bad_out = _port_decl(sub.get("outputs", {}), "outputs")
                    out[n["ref"]] = {"inputs": ins, "outputs": outs, "name": str(sub.get("name", ""))}
                    if bad_in or bad_out:
                        out[n["ref"]]["error"] = "；".join(bad_in + bad_out)
                except (OSError, json.JSONDecodeError, AttributeError):
                    out[n["ref"]] = {"inputs": {}, "outputs": {}, "error": "无法读取"}
            if isinstance(n.get("body"), dict):
                walk(n["body"], d)

    walk(body.workflow, base)
    return out


@app.post("/api/writes")
def write_modules(body: WorkflowBody):
    """运行前要确认的模块（"模块id@主版本"），包括循环体和引用的子流程里的，解析引用的方式与执行引擎相同。
    既含写文件/改动电脑的写模块，也含读电脑上文件的读模块（运行他人工作流等同运行他人程序，读文件也可能外泄）。
    返回仍是模块键的列表（原有字段不变，只是新增了 read 类）；界面按各自已有的模块表查 side_effects 决定如何措辞。
    网页先问完再运行，不会因为执行到一半才发现要确认而把前面的节点重跑一遍。"""
    reg, found, seen = load_registry(), set(), set()

    def walk(g, d: Path):
        for n in g.get("nodes", []) if isinstance(g, dict) else []:
            if not isinstance(n, dict):
                continue
            spec = reg.get(n.get("module")) if n.get("kind") == "module" else None
            if spec and spec.data["side_effects"] in ("read", "write"):
                found.add(n["module"])
            if isinstance(n.get("body"), dict):
                walk(n["body"], d)
            if n.get("kind") == "subflow" and isinstance(n.get("ref"), str):
                p = (d / n["ref"]).resolve()
                if p in seen:
                    continue
                seen.add(p)
                try:
                    walk(json.loads(p.read_text(encoding="utf-8")), p.parent)
                except (OSError, json.JSONDecodeError):
                    pass  # 读不了的引用运行时会报错，这里不用管

    walk(body.workflow, _base(body.source))
    return sorted(found)


def _port_decl(decl, which: str) -> tuple[dict, list[str]]:
    """只留下形状正确的端口声明（{"名字": {"type": 合法类型, ...}}），其余记为问题，不交给前端。"""
    if not isinstance(decl, dict):
        return {}, [f"{which} 不是对象"]
    ok, bad = {}, []
    for name, p in decl.items():
        if isinstance(p, dict) and isinstance(p.get("type"), str) and types.is_valid(p["type"]):
            ok[name] = p
        else:
            bad.append(f"{which}.{name} 的声明不合法")
    return ok, bad


# ---------- 流式：生成与运行 ----------
STREAM_QUEUE = 1000
def _stream(request: Request, work):
    """在后台线程里跑 work(emit, should_stop)，把 emit 的事件逐行送给前端。
    前端断开连接（点停止、关页面）时设置取消标志，生成器、运行检查和执行引擎会在下一步之前停下，
    正在运行的模块进程会被结束。"""
    q: queue.Queue = queue.Queue(maxsize=STREAM_QUEUE)  # 有上限：前端读得慢时后台工作会等一等
    cancel = threading.Event()

    def put(item):
        """可取消的放入：队列满时等待，连接断开后立即放弃，不会把后台线程永远堵住。"""
        while not cancel.is_set():
            try:
                q.put(item, timeout=0.2)
                return
            except queue.Full:
                continue

    def emit(ev):
        put(ev)

    def target():
        try:
            work(emit, cancel.is_set)
        except Exception as e:  # 把意外错误也告诉前端，而不是让连接无声断开
            put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            put(None)

    threading.Thread(target=target, daemon=True).start()

    async def lines():
        try:
            while True:
                try:
                    item = q.get_nowait()
                except queue.Empty:
                    if await request.is_disconnected():  # 用户点了停止或关了页面
                        return
                    await asyncio.sleep(0.05)
                    continue
                if item is None:
                    return
                yield json.dumps(item, ensure_ascii=False) + "\n"
        finally:
            cancel.set()  # 无论正常结束还是断开，都让后台工作停下

    return StreamingResponse(lines(), media_type="application/x-ndjson")


class RefineBody(BaseModel):
    idea: str
    model: str | None = None  # 项目自己选的模型；不给用默认
    answers: list[dict] = []  # [{slot, ask, answer}]
    card: dict | None = None
    spec: dict | None = None  # 上一轮返回的标准需求单，原样送回


@app.post("/api/refine")
def refine_requirement(body: RefineBody):
    """帮使用者理清需求：返回标准需求单、卡片、这一轮要问的问题（最多 3 个）、还缺什么、做不到的部分和交给生成的需求文字。"""
    if not body.idea.strip():
        raise HTTPException(400, "先写一句你想让它做什么")
    cfg = model_config(body.model)
    try:
        return refine_tool.refine(body.idea, body.answers, body.card, body.spec, cfg["generator"], cfg["generator_url"])
    except refine_tool.RefineError as e:
        raise HTTPException(502, str(e)) from None
    except OSError as e:  # 模型服务没开或连不上
        raise HTTPException(503, f"连不上模型服务：{e}") from None


class GenerateBody(BaseModel):
    requirement: str
    check: bool = True
    hint: str = ""
    spec: dict | None = None  # 理清需求得到的标准需求单（卡片改过时不传）
    model: str | None = None  # 项目自己选的模型；不给用默认


@app.post("/api/generate")
def generate(body: GenerateBody, request: Request):
    if not body.requirement.strip():
        raise HTTPException(400, "需求不能为空")

    def work(emit, should_stop):
        cfg = model_config(body.model)
        gen.OLLAMA = cfg["generator_url"]
        emit({"type": "start", "models": {**cfg, "generator_label": llm.label(cfg["generator"]),
                                           "judge_label": llm.label(cfg["judge"])}})
        checker = make_checker(body.requirement, cfg["judge"], cfg["judge_url"], body.hint,
                               should_stop=should_stop) if body.check else None
        res = gen.generate(body.requirement, cfg["generator"], rounds=3, post_check=checker, on_event=emit,
                           should_stop=should_stop, spec=body.spec)
        log = res.get("log") or []
        last = log[-1] if log else {}
        valid = isinstance(res["workflow"], dict) and not last.get("errors")  # 最后一版是否通过结构校验
        emit({"type": "done", "ok": res["ok"], "valid": valid, "rounds": res["rounds"], "workflow": res["workflow"],
              "inconclusive": res.get("inconclusive", False), "reason": res.get("reason", ""),
              "stopped": res.get("stopped", False)})

    return _stream(request, work)


class RunBody(BaseModel):
    workflow: dict
    inputs: dict = {}
    source: str = "saved"
    allow_write: list[str] = []  # 使用者在网页上确认过、允许写文件的模块（"模块id@主版本"）
    run_id: str = ""  # 前端给这次运行起的名字：有了它才能用下面的接口暂停、继续、终止


# 正在进行、带名字的运行：run_id → {"pause": Event, "stop": Event, "seq": 已处理的最新操作序号}
RUNS: dict[str, dict] = {}
RUNS_LOCK = threading.Lock()
# 终止请求比运行请求先到（刚开始就点了终止）：记下 run_id → 时间，随后到达的这次运行直接按已终止处理；一分钟后作废
STOPPED_EARLY: dict[str, float] = {}
EARLY_TTL = 60


@app.post("/api/run")
def run(body: RunBody, request: Request):
    base = _base(body.source)
    ctl = {"pause": threading.Event(), "stop": threading.Event(), "seq": 0, "answers": {}, "answered": threading.Event()}
    if body.run_id:
        with RUNS_LOCK:
            if body.run_id in RUNS:
                raise HTTPException(409, "这个运行名字已经在用")
            RUNS[body.run_id] = ctl
            if STOPPED_EARLY.pop(body.run_id, None) is not None:
                ctl["stop"].set()

    def work(emit, should_stop):
        try:
            # 写文件的模块只有在网页上确认过才运行；没确认的照常拒绝（not_confirmed）
            allowed = set(body.allow_write)
            stopped = lambda: should_stop() or ctl["stop"].is_set()  # noqa: E731

            def ask(path, prompt):
                """“提醒确认”这类模块：等界面通过 /api/run/{run_id}/answer 把回答送来；停止了就返回 None"""
                while not stopped():
                    with RUNS_LOCK:
                        if path in ctl["answers"]:
                            return ctl["answers"].pop(path)
                    ctl["answered"].wait(0.2)
                    ctl["answered"].clear()
                return None
            def confirm(spec, path, target=None):
                # 读/写模块首次运行前：网页已在 /api/writes 里问过并放行的才通过；
                # 把 side_effects 和目标路径带进事件（数据层，界面以后可据此显示“会读取/改动哪个文件”）
                key = f"{spec.id}@{spec.major}"
                ok = key in allowed
                emit({"type": "confirm", "path": path, "module": key,
                      "side_effects": spec.data["side_effects"], "target": target, "ok": ok})
                return ok
            engine = Engine(on_event=emit, confirm=confirm,
                            should_stop=stopped, should_pause=ctl["pause"].is_set, ask=ask if body.run_id else None)
            try:
                res = engine.run(body.workflow, body.inputs, base)
            except WorkflowInvalid as e:
                emit({"type": "invalid", "errors": e.errors})
                return
            out = res.to_json()
            if ctl["stop"].is_set() and (out.get("error") or {}).get("code") == "cancelled":
                out["status"] = "stopped"  # 使用者点了终止（断开连接的停止没人看结果，不区分）
            emit({"type": "result", **out})
        finally:
            if body.run_id:
                with RUNS_LOCK:
                    if RUNS.get(body.run_id) is ctl:
                        del RUNS[body.run_id]

    return _stream(request, work)


class ControlBody(BaseModel):
    seq: int = 0  # 前端每发一个操作加一：同时在路上的几个请求先后到达时，比已处理的旧的暂停、继续不再生效
    path: str = ""        # answer：回答的是哪个节点（ask 事件里的 path）
    answer: dict | None = None  # answer：使用者的回答 {choice, done, note}


@app.post("/api/run/{run_id}/{action}")
def control_run(run_id: str, action: str, body: ControlBody | None = None):
    """暂停（当前节点跑完后停在下一个节点前）、继续、终止（正在运行的模块进程会被结束）一次正在进行的运行；
    answer：把使用者在提示窗里的回答交给正在等的“提醒确认”节点"""
    if action not in ("pause", "resume", "stop", "answer"):
        raise HTTPException(404, "没有这个操作")
    seq = body.seq if body else 0
    with RUNS_LOCK:
        ctl = RUNS.get(run_id)
        if action == "answer":
            if ctl is None:
                raise HTTPException(404, "这次运行已经结束")
            if not body or not body.path:
                raise HTTPException(400, "缺少 path")
            ctl["answers"][body.path] = body.answer if isinstance(body.answer, dict) else {}
            ctl["answered"].set()
            return {"ok": True}
        if ctl is None:
            if action == "stop":
                now = time.monotonic()
                for k, t in list(STOPPED_EARLY.items()):
                    if now - t > EARLY_TTL:
                        del STOPPED_EARLY[k]
                STOPPED_EARLY[run_id] = now
            raise HTTPException(404, "这次运行已经结束")
        if action == "stop":
            ctl["stop"].set()  # 终止总是生效
        elif seq and seq <= ctl["seq"]:
            return {"ok": True, "stale": True}
        elif action == "pause":
            ctl["pause"].set()
        else:
            ctl["pause"].clear()
        ctl["seq"] = max(ctl["seq"], seq)
    return {"ok": True}


# ---------- 设置面板：思考模块默认模型、自定义云端服务商（存在 model_settings.json，不进 git） ----------
_PROVIDER_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _settings_view() -> dict:
    s = llm.load_settings()
    custom = s.get("apis") if isinstance(s.get("apis"), dict) else {}
    builtin = llm.builtin_api_keys()
    return {
        "think_model": s.get("think_model") if isinstance(s.get("think_model"), str) else "",
        "think_default": llm.think_model(),
        "think_choices": llm.ollama_models(llm.default_ollama()),
        "custom_apis": [{"key": k, "name": v.get("name") or k, "base": v.get("base", ""), "key_env": v.get("key_env", ""),
                         "models": v.get("models") or [], "overrides_builtin": k in builtin}
                        for k, v in custom.items() if isinstance(v, dict)],
        "settings_file": str(llm.SETTINGS),
    }


class SettingsBody(BaseModel):
    think_model: str | None = None  # 空字符串：回到自动（已装的第一个）


class CustomApiBody(BaseModel):
    key: str
    name: str
    base: str
    key_env: str
    models: list[str] = []
    json_mode: bool = True


@app.get("/api/settings")
def get_settings():
    return _settings_view()


@app.put("/api/settings")
def put_settings(body: SettingsBody):
    if body.think_model is not None:
        llm.save_settings(think_model=body.think_model.strip() or None)
    return _settings_view()


@app.post("/api/settings/custom-api")
def add_custom_api(body: CustomApiBody):
    key = body.key.strip().lower()
    if not _PROVIDER_KEY.fullmatch(key):
        raise HTTPException(400, "编号只能用小写字母、数字、下划线和短横线，字母开头，最长 32 个字符")
    if not body.name.strip():
        raise HTTPException(400, "名称不能为空")
    base = body.base.strip().rstrip("/")
    if not re.fullmatch(r"https?://\S+", base):
        raise HTTPException(400, "接口地址要以 http:// 或 https:// 开头")
    env = body.key_env.strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", env):
        raise HTTPException(400, "密钥变量名只能用大写字母、数字和下划线，例如 MY_PROXY_KEY")
    models = [m.strip() for m in body.models if isinstance(m, str) and m.strip()]
    if not models:
        raise HTTPException(400, "至少写一个模型名")
    if key in llm.builtin_api_keys():
        raise HTTPException(400, f"“{key}”是内置服务商的编号，换一个编号（内置的在“配置 AI”里直接用）")

    def apply(s):
        apis = dict(s.get("apis") or {}) if isinstance(s.get("apis"), dict) else {}
        apis[key] = {"name": body.name.strip()[:40], "base": base, "key_env": env, "models": models, "json_mode": bool(body.json_mode)}
        s["apis"] = apis
    llm.update_settings(apply)
    llm.forget_listed(key)
    return _settings_view()


@app.delete("/api/settings/custom-api/{key}")
def delete_custom_api(key: str):
    found = []

    def apply(s):
        apis = dict(s.get("apis") or {}) if isinstance(s.get("apis"), dict) else {}
        if key in apis:
            found.append(key)
            apis.pop(key)
            if apis:
                s["apis"] = apis
            else:
                s.pop("apis", None)
    llm.update_settings(apply)
    if not found:
        raise HTTPException(404, "没有这个自定义服务商")
    llm.forget_listed(key)
    return _settings_view()


# ---------- 首次启动引导：配置 AI（见 tools/setup_ai.py） ----------
@app.get("/api/setup/state")
def setup_state():
    return {"done": setup_ai.done()}


@app.post("/api/setup/finish")
def setup_finish():
    """不配置 AI、直接手动搭建：也算完成了初始化，下次直接进主程序"""
    setup_ai.finish()
    return {"done": True}


@app.get("/api/setup/providers")
def setup_providers():
    return setup_ai.providers()


class TestApiBody(BaseModel):
    provider: str
    key: str


@app.post("/api/setup/test-api")
def setup_test_api(body: TestApiBody):
    return setup_ai.test_api(body.provider, body.key)


class CliBody(BaseModel):
    cli: str


@app.post("/api/setup/check-cli")
def setup_check_cli(body: CliBody):
    return setup_ai.check_cli(body.cli)


class SetupSaveBody(BaseModel):
    model: str
    key: str | None = None


@app.post("/api/setup/save")
def setup_save(body: SetupSaveBody):
    try:
        setup_ai.save(body.model, body.key)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except keystore.KeystoreError as e:
        raise HTTPException(500, str(e)) from None
    return {"chosen": body.model}


@app.get("/api/setup/local")
def setup_local():
    return setup_ai.local_models()


class SetupModelBody(BaseModel):
    model: str


@app.post("/api/setup/probe")
def setup_probe(body: SetupModelBody):
    return setup_ai.probe(body.model)


@app.post("/api/setup/run-test")
def setup_run_test(body: SetupModelBody):
    return setup_ai.run_test(body.model)


@app.get("/api/setup/scan")
def setup_scan():
    return setup_ai.scan()


class InstallBody(BaseModel):
    tag: str


@app.post("/api/setup/install")
def setup_install(body: InstallBody, request: Request):
    """安装运行环境和模型，逐行返回进度 {step, pct, text}；最后一行 {type: done} 或 {type: error}。前端断开即取消"""
    model = next((m for m in setup_ai.catalog().get("models") or [] if isinstance(m, dict) and m.get("tag") == body.tag),
                 None)
    if model is None:
        raise HTTPException(400, "模型目录里没有这个模型")

    def work(emit, should_stop):
        try:
            setup_ai.install(model, emit, should_stop)
        except setup_ai.Cancelled:
            return
        except (OSError, RuntimeError, ValueError) as e:
            emit({"type": "error", "message": str(e)})
            return
        emit({"type": "done"})

    return _stream(request, work)


# ---------- 前端页面 ----------
if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def index(path: str):
        if path.startswith("api/"):  # 不存在的接口应当报 404，而不是返回网页
            raise HTTPException(404, "没有这个接口")
        # 不让浏览器或桌面窗口缓存首页：缓存了旧首页就会一直加载旧版脚本，重新构建后也看不到改动
        return FileResponse(WEB_DIST / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
