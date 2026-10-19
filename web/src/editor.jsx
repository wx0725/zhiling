// 编辑用的面板组件：改参数、看/删连线、添加节点菜单、程序（JSON）视图。
import { useEffect, useRef, useState } from "react";
import { parseValue, toRaw } from "./form.js";
import { typeLabel } from "./labels.js";

// 模块节点的参数：接了线的显示来源；没接线的可以填固定值，留空表示用默认值
export function ParamEditor({ module, config = {}, incoming, onSet }) {
  return (
    <div className="params">
      {Object.entries(module.inputs).map(([k, p]) => (
        <Param key={k} name={k} decl={p} value={config[k]} has={Object.hasOwn(config, k)} from={incoming[k]} onSet={(v) => onSet(k, v)} />
      ))}
    </div>
  );
}

function Param({ name, decl, value, has, from, onSet }) {
  const shown = has ? toRaw(decl.type, value) : decl.type === "boolean" ? null : "";
  const [raw, setRaw] = useState(shown);
  const [error, setError] = useState("");
  useEffect(() => { setRaw(shown); setError(""); }, [shown]); // 撤销、重做后跟着程序走
  const required = decl.required !== false && !("default" in decl);
  const placeholder = "default" in decl && decl.default !== null ? `默认：${toRaw(decl.type, decl.default)}` : required ? "必填：填固定值或接一条线" : "可不填";

  const commit = (next = raw) => {
    if (next === null || next === undefined || (decl.type !== "boolean" && String(next).trim() === "")) {
      setError("");
      if (has) onSet(undefined);
      return;
    }
    try {
      const v = parseValue(decl.type, next);
      setError("");
      if (!has || JSON.stringify(v) !== JSON.stringify(value)) onSet(v);
    } catch (e) {
      setError(e.message);
    }
  };

  const id = `param-${name}`;
  const multi = decl.type.startsWith("list<") || decl.type === "object" || decl.type === "any" || (decl.type === "string" && String(raw || "").length > 40);
  return (
    <div className="field param">
      <label htmlFor={id}>
        <span className="port-name">{name}</span>
        <span className="type" title={decl.type}>{typeLabel(decl.type)}</span>
      </label>
      {decl.description && <p className="param-desc">{decl.description}</p>}
      {from ? (
        <p className="param-wired">由连线提供：来自 <code>{from}</code></p>
      ) : decl.type === "boolean" ? (
        <select id={id} value={raw === null || raw === undefined ? "" : String(raw)}
                onChange={(e) => { const v = e.target.value === "" ? null : e.target.value === "true"; setRaw(v); commit(v); }}>
          <option value="">{placeholder}</option><option value="true">是</option><option value="false">否</option>
        </select>
      ) : multi ? (
        <textarea id={id} rows={2} value={raw ?? ""} placeholder={placeholder} onChange={(e) => setRaw(e.target.value)} onBlur={(e) => commit(e.target.value)} />
      ) : (
        <input id={id} value={raw ?? ""} placeholder={placeholder} onChange={(e) => setRaw(e.target.value)} onBlur={(e) => commit(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") commit(e.currentTarget.value); }} />
      )}
      {error && <p className="field-error">{error}</p>}
    </div>
  );
}

// 节点名称：留空用模块名
export function LabelEditor({ label, fallback, onSet }) {
  const [raw, setRaw] = useState(label || "");
  useEffect(() => setRaw(label || ""), [label]);
  const commit = (v = raw) => { if ((v || "").trim() !== (label || "")) onSet(v); };
  return (
    <div className="field">
      <label htmlFor="node-label">名称</label>
      <input id="node-label" value={raw} placeholder={fallback} onChange={(e) => setRaw(e.target.value)} onBlur={(e) => commit(e.target.value)}
             onKeyDown={(e) => { if (e.key === "Enter") commit(e.currentTarget.value); }} />
    </div>
  );
}

export function MaxItemsEditor({ value, onSet }) {
  const [raw, setRaw] = useState(String(value ?? ""));
  const [error, setError] = useState("");
  useEffect(() => setRaw(String(value ?? "")), [value]);
  const commit = (v = raw) => {
    const n = Number(v);
    if (!Number.isInteger(n) || n < 1) return setError("需要大于 0 的整数");
    setError("");
    if (n !== value) onSet(n);
  };
  return (
    <div className="field">
      <label htmlFor="max-items">最多处理多少项</label>
      <input id="max-items" inputMode="numeric" value={raw} onChange={(e) => setRaw(e.target.value)} onBlur={(e) => commit(e.target.value)}
             onKeyDown={(e) => { if (e.key === "Enter") commit(e.currentTarget.value); }} />
      {error && <p className="field-error">{error}</p>}
    </div>
  );
}

// 选中的连线
export function EdgeDetail({ edge, onDelete }) {
  return (
    <div className="detail">
      <h3>{edge.control ? "控制线" : "连线"}</h3>
      <p className="desc">
        {edge.control ? "条件分支走到这一路时，才运行它指向的节点。" : "把左边节点产生的值，送到右边节点的输入。"}
      </p>
      <p className="edge-ends"><code>{edge.from}</code> → <code>{edge.to}</code></p>
      {edge.scope?.length > 0 && <p className="muted">在循环 {edge.scope.join(" > ")} 里</p>}
      {onDelete && <button type="button" className="btn danger wide" onClick={onDelete}>删除这条线</button>}
      <p className="muted hint">也可以选中后按 Delete 删除。</p>
    </div>
  );
}

// 添加节点菜单：模块按类别分组，另有条件分支、合并、逐项循环
export function AddMenu({ modules, loop, onAdd }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const close = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  const groups = {};
  for (const [key, m] of Object.entries(modules)) (groups[m.category || "其他"] ||= []).push([key, m]);
  const pick = (spec) => { setOpen(false); onAdd(spec); };
  return (
    <div className="add-menu" ref={ref}>
      <button type="button" className="btn small" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        添加节点{loop ? `到循环 ${loop.split("/").pop()}` : ""}
      </button>
      {open && (
        <div className="menu" role="menu">
          {Object.entries(groups).map(([cat, items]) => (
            <div key={cat} className="menu-group">
              <div className="menu-title">{cat}</div>
              {items.map(([key, m]) => (
                <button key={key} type="button" role="menuitem" className="menu-item" onClick={() => pick({ kind: "module", module: key })}>
                  <span>{m.name}</span><span className="menu-sub">{m.description?.split("。")[0]}</span>
                </button>
              ))}
            </div>
          ))}
          <div className="menu-group">
            <div className="menu-title">流程</div>
            <button type="button" role="menuitem" className="menu-item" onClick={() => pick({ kind: "if" })}><span>条件分支</span><span className="menu-sub">按真假走 then 或 else</span></button>
            <button type="button" role="menuitem" className="menu-item" onClick={() => pick({ kind: "merge" })}><span>合并</span><span className="menu-sub">把两条支路合成一个值</span></button>
            {!loop && <button type="button" role="menuitem" className="menu-item" onClick={() => pick({ kind: "foreach" })}><span>逐项循环</span><span className="menu-sub">对列表的每一项做同样的事</span></button>}
          </div>
        </div>
      )}
    </div>
  );
}

// 程序：当前工作流的 JSON 原文——画布上的每个改动都会立刻反映在这里
export function ProgramView({ wf, dirty }) {
  const [copied, setCopied] = useState(false);
  const text = JSON.stringify(wf, null, 2);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="program">
      <div className="program-head">
        <p className="muted">这就是程序本身。画布上的改动会立刻写进这里{dirty ? "（有改动还没保存）" : ""}。</p>
        <button type="button" className="btn small ghost" onClick={copy}>{copied ? "已复制" : "复制"}</button>
      </div>
      <pre className="code">{text}</pre>
    </div>
  );
}

// ---------- 工作流自己的输入、输出 ----------
const TYPES = ["string", "integer", "number", "boolean", "object", "any", "file",
  "list<string>", "list<integer>", "list<number>", "list<boolean>", "list<object>", "list<any>"];
const NAME = /^[a-z][a-z0-9_]*$/;

export function PortsEditor({ side, ports, ops }) {
  const entries = Object.entries(ports || {});
  return (
    <div className="ports-editor">
      {entries.length === 0 && <p className="muted">还没有{side === "inputs" ? "输入" : "输出"}。</p>}
      {entries.map(([name, p]) => <PortCard key={name} side={side} name={name} decl={p} names={Object.keys(ports)} ops={ops} />)}
      <button type="button" className="btn small wide" onClick={ops.add}>添加{side === "inputs" ? "一个输入" : "一个输出"}</button>
    </div>
  );
}

function PortCard({ side, name, decl, names, ops }) {
  const [nm, setNm] = useState(name);
  const [nameErr, setNameErr] = useState("");
  const [desc, setDesc] = useState(decl.description || "");
  const hasDefault = Object.hasOwn(decl, "default");
  const [def, setDef] = useState(hasDefault ? toRaw(decl.type, decl.default) : "");
  const [defErr, setDefErr] = useState("");
  useEffect(() => setNm(name), [name]);
  useEffect(() => setDesc(decl.description || ""), [decl.description]);
  useEffect(() => setDef(hasDefault ? toRaw(decl.type, decl.default) : ""), [hasDefault, decl.default, decl.type]); // eslint-disable-line react-hooks/exhaustive-deps

  const rename = (v) => {
    if (v === name) return setNameErr("");
    if (!NAME.test(v)) return setNameErr("小写英文字母开头，后面跟小写字母、数字或下划线");
    if (names.includes(v)) return setNameErr(`已经有叫 ${v} 的了`);
    setNameErr("");
    ops.rename(name, v);
  };
  const commitDefault = (v) => {
    if (decl.type !== "boolean" && String(v ?? "").trim() === "") {
      setDefErr("");
      if (hasDefault) ops.fields(name, { default: undefined });
      return;
    }
    try {
      const value = parseValue(decl.type, v);
      setDefErr("");
      ops.fields(name, { default: value });
    } catch (e) {
      setDefErr(e.message);
    }
  };
  const optional = decl.required === false || hasDefault;
  const id = `${side}-${name}`;
  return (
    <div className="port-card">
      <div className="field">
        <label htmlFor={`${id}-name`}>名字<span className="type">连线里用它：{side === "inputs" ? "$in" : "$out"}.{name}</span></label>
        <input id={`${id}-name`} value={nm} onChange={(e) => setNm(e.target.value)} onBlur={(e) => rename(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") rename(e.currentTarget.value); }} />
        {nameErr && <p className="field-error">{nameErr}</p>}
      </div>
      <div className="field">
        <label htmlFor={`${id}-type`}>类型</label>
        <select id={`${id}-type`} value={decl.type} onChange={(e) => ops.type(name, e.target.value)}>
          {(TYPES.includes(decl.type) ? TYPES : [decl.type, ...TYPES]).map((t) => <option key={t} value={t}>{typeLabel(t)}（{t}）</option>)}
        </select>
      </div>
      <div className="field">
        <label htmlFor={`${id}-desc`}>说明</label>
        <input id={`${id}-desc`} value={desc} placeholder="给使用者看的说明，例如：要总结的网址" onChange={(e) => setDesc(e.target.value)}
               onBlur={(e) => { if (e.target.value !== (decl.description || "")) ops.fields(name, { description: e.target.value }); }} />
      </div>
      {side === "inputs" && (
        <>
          <label className="check-row">
            <input type="checkbox" checked={optional} disabled={hasDefault}
                   onChange={(e) => ops.fields(name, { required: e.target.checked ? false : undefined })} />
            可以不填{hasDefault ? "（有默认值）" : ""}
          </label>
          <div className="field">
            <label htmlFor={`${id}-default`}>默认值</label>
            {decl.type === "boolean" ? (
              <select id={`${id}-default`} value={hasDefault ? String(decl.default) : ""} onChange={(e) => ops.fields(name, { default: e.target.value === "" ? undefined : e.target.value === "true" })}>
                <option value="">没有默认值</option><option value="true">是</option><option value="false">否</option>
              </select>
            ) : (
              <input id={`${id}-default`} value={def} placeholder="没有默认值" onChange={(e) => setDef(e.target.value)}
                     onBlur={(e) => commitDefault(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") commitDefault(e.currentTarget.value); }} />
            )}
            {defErr && <p className="field-error">{defErr}</p>}
          </div>
        </>
      )}
      <button type="button" className="btn small danger" onClick={() => ops.remove(name)}>删除{side === "inputs" ? "这个输入" : "这个输出"}</button>
    </div>
  );
}

// 没有选中节点时：工作流的名称和说明
export function MetaEditor({ name, intent, onSet }) {
  const [n, setN] = useState(name || "");
  const [i, setI] = useState(intent || "");
  useEffect(() => setN(name || ""), [name]);
  useEffect(() => setI(intent || ""), [intent]);
  return (
    <div className="detail">
      <h3>这个工作流</h3>
      <p className="muted">点画布上的节点可以改它的参数；点“输入”“输出”可以增删工作流的输入和输出。</p>
      <div className="field">
        <label htmlFor="wf-name">名称</label>
        <input id="wf-name" value={n} onChange={(e) => setN(e.target.value)} onBlur={(e) => { if (e.target.value.trim() && e.target.value !== name) onSet({ name: e.target.value.trim() }); }} />
      </div>
      <div className="field">
        <label htmlFor="wf-intent">要做什么</label>
        <textarea id="wf-intent" rows={3} value={i} onChange={(e) => setI(e.target.value)} onBlur={(e) => { if (e.target.value !== (intent || "")) onSet({ intent: e.target.value }); }} />
      </div>
    </div>
  );
}
