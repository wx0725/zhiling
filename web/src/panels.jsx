import { useEffect, useState } from "react";
import { typeClass } from "./nodes.jsx";
import { initialValues, collectInputs } from "./form.js";
import { isRefusal, issueText } from "./flow.js";
import { typeLabel } from "./labels.js";
import { LabelEditor, MaxItemsEditor, ParamEditor } from "./editor.jsx";

// ---------- 运行：按输入类型生成表单 ----------
const isMultiline = (type) => type.startsWith("list<") || type === "object" || type === "any";

export function RunPanel({ wf, running, result, onRun }) {
  const [values, setValues] = useState({});
  const [errors, setErrors] = useState({});

  // 只在输入声明变了时重置（换工作流时整个面板会重新挂载）；在画布上改节点不会清掉已填的参数
  const inputsKey = wf ? JSON.stringify(wf.inputs) : "";
  useEffect(() => {
    if (!wf) return;
    setValues(initialValues(wf.inputs));
    setErrors({});
  }, [inputsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!wf) return <p className="muted">先打开或生成一个工作流。</p>;

  const submit = (e) => {
    e.preventDefault();
    const { values: parsed, errors: errs } = collectInputs(wf.inputs, values);
    setErrors(errs);
    if (!Object.keys(errs).length) onRun(parsed);
  };

  const inputs = Object.entries(wf.inputs);
  return (
    <form className="run" onSubmit={submit}>
      {inputs.length === 0 && <p className="muted">这个工作流不需要输入。</p>}
      {inputs.map(([k, p]) => (
        <div className="field" key={k}>
          <label htmlFor={`in-${k}`}>
            <span className={`dot ${typeClass(p.type)}`} aria-hidden="true" />
            {p.description || k}
            <span className="type" title={p.type}>{typeLabel(p.type)}{p.required === false || "default" in p ? "，可不填" : ""}</span>
          </label>
          {p.type === "boolean" && p.required === false && !("default" in p) ? (
            <select id={`in-${k}`} value={values[k] === null || values[k] === undefined ? "" : String(values[k])}
                    onChange={(e) => setValues({ ...values, [k]: e.target.value === "" ? null : e.target.value === "true" })}>
              <option value="">不填</option><option value="true">是</option><option value="false">否</option>
            </select>
          ) : p.type === "boolean" ? (
            <input id={`in-${k}`} type="checkbox" checked={!!values[k]} onChange={(e) => setValues({ ...values, [k]: e.target.checked })} />
          ) : isMultiline(p.type) ? (
            <textarea id={`in-${k}`} rows={3} value={values[k] ?? ""} onChange={(e) => setValues({ ...values, [k]: e.target.value })} />
          ) : (
            <input id={`in-${k}`} value={values[k] ?? ""} onChange={(e) => setValues({ ...values, [k]: e.target.value })} />
          )}
          {errors[k] && <p className="field-error">{errors[k]}</p>}
        </div>
      ))}
      <button className="btn primary wide" type="submit" disabled={running}>{running ? "运行中…" : "运行"}</button>
      {result ? <RunResult result={result} outputs={wf.outputs} /> : <OutputsPreview outputs={wf.outputs} />}
    </form>
  );
}

// 还没运行时，先告诉使用者这个工作流会得到什么
function OutputsPreview({ outputs }) {
  const items = Object.entries(outputs || {});
  if (!items.length) return null;
  return (
    <div className="preview">
      <h3>运行后会得到</h3>
      <ul>
        {items.map(([k, p]) => (
          <li key={k}>
            <span className={`dot ${typeClass(p.type)}`} aria-hidden="true" />
            <span className="preview-name">{p.description || k}</span>
            <span className="type" title={p.type}>{typeLabel(p.type)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const LONG = 400; // 超过这么多字先收起

function Value({ v }) {
  const text = typeof v === "string" ? v : JSON.stringify(v, null, 2);
  const [open, setOpen] = useState(false);
  if (text === undefined) return <pre className="value muted">（没有值）</pre>;
  const long = text.length > LONG;
  return (
    <>
      <pre className={`value ${long && !open ? "clamped" : ""}`}>{long && !open ? text.slice(0, LONG) + "…" : text}</pre>
      {long && (
        <button type="button" className="link" onClick={() => setOpen(!open)}>
          {open ? "收起" : `展开全文（${text.length} 字）`}
        </button>
      )}
    </>
  );
}

function RunResult({ result, outputs = {} }) {
  const ok = result.status === "completed";
  return (
    <div className={`result ${ok ? "ok" : "bad"}`} role="status">
      <h3>{ok ? `完成，用时 ${result.seconds} 秒` : "没有完成"}</h3>
      {result.error && (
        <p className="error-text">
          {result.error.path ? `在节点 ${result.error.path} 出错：` : ""}{result.error.message}
        </p>
      )}
      {(result.warnings || []).map((w) => <p key={w} className="muted">{w}</p>)}
      {Object.entries(result.outputs || {}).map(([k, v]) => (
        <div key={k} className="out">
          <div className="out-name" title={k}>{outputs[k]?.description || k}</div>
          <Value v={v} />
        </div>
      ))}
    </div>
  );
}

// ---------- 生成过程：每一轮做了什么 ----------
// onFocus(画布节点 id)：在图上定位；targets(问题文字, 轮次) → 能在当前画布上找到的节点 id
export function GenerationLog({ events, generating, targets = () => [], onFocus, drawnRound }) {
  if (!events.length) return <p className="muted">在上面输入一句话需求，点“生成工作流”，这里会显示每一轮的起草、校验和运行检查。</p>;
  const start = events.find((e) => e.type === "start");
  const rounds = {};
  const tail = [];
  for (const e of events) {
    if (e.round) (rounds[e.round] ||= []).push(e);
    else if (e.type !== "start") tail.push(e);
  }
  const done = tail.find((e) => e.type === "done");
  const errors = tail.filter((e) => e.type === "error");
  const nums = Object.keys(rounds).map(Number).sort((a, b) => a - b);
  return (
    <div className="genlog">
      {done && <p className={`gen-banner ${done.ok ? "ok" : "bad"}`} role="status">{doneText(done, drawnRound) || "生成结束。"}</p>}
      {errors.map((e, i) => <p key={i} className="gen-banner bad" role="alert">{e.message}</p>)}
      {!done && !errors.length && generating && <p className="gen-banner busy" role="status">正在生成…</p>}
      {start && <p className="muted gen-models">起草：{start.models.generator}　审查：{start.models.judge}</p>}
      {tail.filter((e) => e.type === "feasibility").map((e, i) => (
        <p key={i} className={e.ok ? "gen-feasible" : "error-text"}>{e.ok ? "现有模块能做到这件事。" : `做不到：${e.reason}`}</p>
      ))}
      <ol className="rounds">
        {nums.map((r, i) => (
          <Round key={r} n={r} events={rounds[r]} last={i === nums.length - 1} targets={(x) => targets(x, r)} onFocus={onFocus} />
        ))}
      </ol>
    </div>
  );
}

function doneText(e, drawnRound) {
  if (e.ok) return isRefusal(e.workflow) ? "" : `用了 ${e.rounds} 轮，已显示在画布上，可以运行或保存。`; // 拒绝的原因已在上面显示
  if (e.stopped) return "已停止生成。";
  const shown = drawnRound ? `画布上是第 ${drawnRound} 轮那一版，可以运行看看，但不能保存。` : "画布上没有可用的版本。";
  const where = e.valid ? "最后一版没有通过实跑检查，" : "最后一版没有通过结构校验，";
  if (e.inconclusive) return `运行检查没有得出结论（${e.reason || "原因不明"}）。${shown}`;
  return `${e.rounds} 轮之后仍有问题：${where}${shown}`;
}

// 一轮：起草 → 结构校验 → 实跑检查；没通过的交回模型重写
function Round({ n, events, last, targets, onFocus }) {
  const validated = events.find((e) => e.type === "validated");
  const checking = events.find((e) => e.type === "checking");
  const checked = events.find((e) => e.type === "checked");
  const errs = validated?.errors || [];
  const problems = checked?.problems || [];
  const rejected = errs.length > 0 || checked?.status === "failed";
  return (
    <li className="round">
      <div className="round-n">第 {n} 轮</div>
      <ol className="steps">
        <Step state={validated ? "completed" : "running"} title="起草" note={validated ? `${validated.seconds} 秒` : "模型正在写图…"} />
        {validated && (
          <Step state={errs.length ? "failed" : "completed"} title="结构校验" note={errs.length ? `发现 ${errs.length} 个问题` : "通过"}>
            <Issues items={errs} targets={() => []} />
          </Step>
        )}
        {checking && (
          <Step
            state={!checked ? "running" : { passed: "completed", failed: "failed", inconclusive: "skipped" }[checked.status] || "skipped"}
            title="实跑检查"
            note={!checked ? "用示例输入实际运行，再由审查模型检查…"
              : checked.status === "passed" ? `用 ${(checked.samples || []).length} 组示例输入运行，没有发现问题${checked.seconds ? `（${checked.seconds} 秒）` : ""}`
              : checked.status === "failed" ? `发现 ${problems.length} 个问题`
              : `没有得出结论：${checked.reason || "原因不明"}`}>
            {checked?.status === "failed" && <Issues items={problems} targets={targets} onFocus={onFocus} />}
            {(checked?.samples || []).length > 0 && (
              <details className="samples">
                <summary>用到的示例输入</summary>
                {checked.samples.map((x, i) => <pre key={i} className="value">{JSON.stringify(x, null, 2)}</pre>)}
              </details>
            )}
          </Step>
        )}
      </ol>
      {rejected && !last && <p className="round-next">把这些问题和改法交回模型，重写一版 ↓</p>}
    </li>
  );
}

function Step({ state, title, note, children }) {
  return (
    <li className={`step s-${state}`}>
      <span className={`led led-${state}`} aria-hidden="true" />
      <div className="step-body">
        <div><span className="step-title">{title}</span><span className="step-note">{note}</span></div>
        {children}
      </div>
    </li>
  );
}

// 问题列表：能在图上找到的，给出“在图上看”
function Issues({ items, targets, onFocus }) {
  if (!items.length) return null;
  return (
    <ul className="issues">
      {items.map((x) => {
        const ids = onFocus ? targets(x) : [];
        return (
          <li key={x} title={x}>
            {issueText(x)}
            {ids.map((id) => (
              <button key={id} type="button" className="link focus-node" onClick={() => onFocus(id)}>在图上看 {id.split("/").pop()}</button>
            ))}
          </li>
        );
      })}
    </ul>
  );
}

// ---------- 节点详情 ----------
const PAGE = 20; // 循环节点的记录一次显示多少条

export function NodeDetail(props) {
  // 换了节点就从第一页看起
  return <NodeDetailBody key={props.node?.id || ""} {...props} />;
}

const STATE_TEXT = { idle: "还没运行", running: "运行中", completed: "完成", skipped: "跳过", failed: "出错" };

function NodeDetailBody({ node, status, records, edits }) {
  const [shown, setShown] = useState(PAGE);
  if (!node) return <p className="muted">点画布上的节点，查看它做什么、收到了什么、产生了什么。</p>;
  const d = node.data;
  const n = d.node;
  const m = d.module;
  // 端口名 → 含义：显示时用含义作说明，端口名本身仍保留（它和图里的连线对应）
  const meaning = (side, k) => (m?.[side]?.[k]?.description) || [...(d.ins || []), ...(d.outs || [])].find((p) => p.name === k)?.desc || "";
  // 收到的参数：接了线或写了固定值的（画布上显示的那些）在前；其余是模块默认值，收起
  const shownPorts = new Set((d.ins || []).map((p) => p.name));
  const main = (r) => Object.entries(r.inputs || {}).filter(([k]) => shownPorts.has(k) || !m);
  const rest = (r) => (m ? Object.entries(r.inputs || {}).filter(([k]) => !shownPorts.has(k)) : []);
  const state = status?.state || "idle";
  const loop = records?.some((r) => r.path.includes("["));
  return (
    <div className="detail">
      <div className="detail-head">
        <span className={`led led-${state}`} aria-hidden="true" />
        <div className="detail-titles">
          <h3>{n?.label || m?.name || d.title || node.id}</h3>
          <p className="detail-sub">
            {m ? `${m.name}模块` : { if: "条件分支", merge: "合并", foreach: "逐项循环", subflow: "子流程" }[n?.kind] || "工作流的端点"}
            {n?.module && <code title="模块写法">{n.module}</code>}
          </p>
        </div>
      </div>
      {m && <p className="desc">{m.description}</p>}

      <p className={`detail-state s-${state}`}>
        {STATE_TEXT[state]}
        {status?.counts ? `：共 ${status.counts.total} 项，完成 ${status.counts.completed}，跳过 ${status.counts.skipped}，出错 ${status.counts.failed}` : ""}
        {!status?.counts && records?.length === 1 && records[0].status === "completed" ? `，用时 ${records[0].seconds} 秒` : ""}
      </p>
      {status?.reason && <p className="muted">{status.reason}</p>}

      {edits && (
        <section className="detail-section editing">
          <h4>修改</h4>
          <LabelEditor label={edits.node.label} fallback={m?.name || d.title || node.id} onSet={edits.setLabel} />
          {edits.node.kind === "foreach" && <MaxItemsEditor value={edits.node.max_items} onSet={edits.setMaxItems} />}
          {m && (
            <>
              <h5>参数</h5>
              <ParamEditor module={m} config={edits.node.config || {}} incoming={edits.incoming} onSet={edits.setConfig} />
            </>
          )}
          <button type="button" className="btn danger wide" onClick={edits.remove}>删除这个节点</button>
          <p className="muted hint">连着它的线会一起删除。可以用“撤销”恢复。</p>
        </section>
      )}

      {!edits && n?.config && Object.keys(n.config).length > 0 && (
        <section className="detail-section">
          <h4>固定参数</h4>
          {Object.entries(n.config).map(([k, v]) => <Field key={k} name={k} hint={meaning("inputs", k)} v={v} />)}
        </section>
      )}

      {(records || []).slice(0, shown).map((r, i) => {
        const body = (
          <>
            {r.reason && <p className="muted">{r.reason}</p>}
            {main(r).length > 0 && <h5>收到</h5>}
            {main(r).map(([k, v]) => <Field key={"i" + k} name={k} hint={meaning("inputs", k)} v={v} />)}
            {rest(r).length > 0 && (
              <details className="defaults">
                <summary>另有 {rest(r).length} 个参数用的是默认值</summary>
                {rest(r).map(([k, v]) => <Field key={"d" + k} name={k} hint={meaning("inputs", k)} v={v} />)}
              </details>
            )}
            {Object.keys(r.outputs || {}).length > 0 && <h5>产生</h5>}
            {Object.entries(r.outputs || {}).map(([k, v]) => <Field key={"o" + k} name={k} hint={meaning("outputs", k)} v={v} />)}
          </>
        );
        if (!loop) return <section key={r.path} className="detail-section"><h4>这次运行</h4>{body}</section>;
        const item = Number(r.path.match(/\[(\d+)\]/)[1]) + 1;
        return (
          <details key={r.path} className={`record s-${r.status}`} open={i === 0}>
            <summary>
              <span className={`led led-${r.status}`} aria-hidden="true" />
              第 {item} 项：{STATE_TEXT[r.status]}{r.status === "completed" ? `，${r.seconds} 秒` : ""}
            </summary>
            {body}
          </details>
        );
      })}
      {records && records.length > shown && (
        <button type="button" className="btn ghost wide" onClick={() => setShown(shown + PAGE)}>
          再显示 {Math.min(PAGE, records.length - shown)} 条（共 {records.length} 条）
        </button>
      )}
      {!records && <p className="muted">运行之后，这里会显示它每次收到的输入和产生的输出。</p>}
    </div>
  );
}

// 一个端口的值：名字、含义、值；长文本先收起
function Field({ name, hint, v }) {
  return (
    <div className="out">
      <div className="out-name"><span className="port-name">{name}</span>{hint && <span className="port-hint">{hint}</span>}</div>
      <Value v={v} />
    </div>
  );
}
