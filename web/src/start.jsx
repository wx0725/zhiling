// 开始页：没打开工作流时显示。三条路——理清需求再生成（重点）、从示例开始、从空白开始。
import { useEffect, useRef, useState } from "react";
import { postJSON } from "./api.js";
import { CARD_PARTS, cardToRequirement, mergeAnswers } from "./flow.js";

export function StartPage({ idea, setIdea, generating, examples, onGenerate, onOpen, onBlank }) {
  const [result, setResult] = useState(null); // {card, questions, unsupported, requirement}
  const [card, setCard] = useState(null); // 使用者可改的卡片
  const [answers, setAnswers] = useState([]); // 已经回答过的 [{ask, answer}]
  const [chosen, setChosen] = useState({}); // 本轮：问题序号 → 答案
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ctrl = useRef(null);
  useEffect(() => () => ctrl.current?.abort(), []);

  const cardEdited = result && JSON.stringify(card) !== JSON.stringify(result.card);

  const ask = async (withAnswers) => {
    if (!idea.trim() || busy) return;
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setBusy(true);
    setError("");
    try {
      // 只在使用者改过卡片时把卡片交回去；否则模型会把上一轮自己整理的卡片当成定论，忽略新的回答
      const r = await postJSON("/api/refine", { idea, answers: withAnswers, card: cardEdited ? card : null }, c.signal);
      setResult(r);
      setCard(r.card);
      setAnswers(withAnswers);
      setChosen({});
    } catch (e) {
      if (e.name !== "AbortError") setError(`没能整理：${e.message}`);
    } finally {
      if (ctrl.current === c) setBusy(false);
    }
  };
  const stop = () => {
    ctrl.current?.abort();
    setBusy(false);
  };
  const restart = () => {
    stop();
    setResult(null);
    setCard(null);
    setAnswers([]);
    setChosen({});
    setError("");
  };
  const finalText = result ? (cardEdited ? cardToRequirement(card) : result.requirement) : idea;

  return (
    <div className="start">
      <section className="start-ask">
        <h2>你想让它做什么？</h2>
        <p className="muted">说说你会给它什么、想得到什么。说不清也没关系，点“帮我理清需求”，它会问你几个关键问题。</p>
        <label className="sr-only" htmlFor="idea">想法</label>
        <textarea id="idea" rows={3} value={idea} placeholder="例如：帮我看看这几个网页都讲了什么"
                  onChange={(e) => setIdea(e.target.value)} disabled={busy} />
        <div className="start-actions">
          {busy
            ? <button type="button" className="btn ghost" onClick={stop}>停止整理</button>
            : <button type="button" className="btn primary" onClick={() => ask([])} disabled={!idea.trim() || generating}>{result ? "重新整理" : "帮我理清需求"}</button>}
          <button type="button" className="btn" onClick={() => onGenerate(idea)} disabled={!idea.trim() || busy || generating}>直接生成</button>
          {busy && <span className="working" role="status">正在整理需求，通常要十几秒…</span>}
        </div>
        {error && <p className="error-text" role="alert">{error}</p>}
      </section>

      {result && (
        <section className="refine" aria-label="理清后的需求">
          {result.questions.length > 0 && (
            <div className="questions">
              <h3>还需要你确定</h3>
              {result.questions.map((q, i) => (
                <Question key={q.ask} q={q} value={chosen[i] || ""} onChange={(v) => setChosen({ ...chosen, [i]: v })} />
              ))}
              <button type="button" className="btn" onClick={() => ask(mergeAnswers(answers, result.questions, chosen))}
                      disabled={busy || !Object.values(chosen).some((v) => String(v).trim())}>根据回答更新</button>
            </div>
          )}

          <div className="req-card">
            <h3>需求卡片{cardEdited ? "（改过）" : ""}</h3>
            {CARD_PARTS.map(([k, label]) => (
              <CardPart key={k} label={label} items={card[k] || []} onChange={(items) => setCard({ ...card, [k]: items })} />
            ))}
          </div>

          {result.unsupported.length > 0 && (
            <div className="unsupported" role="note">
              <h3>现有模块做不到</h3>
              <ul>{result.unsupported.map((x) => <li key={x}>{x}</li>)}</ul>
              <p className="muted">这些部分不会出现在生成的工作流里。</p>
            </div>
          )}

          <div className="final">
            <h3>将按这段需求生成</h3>
            <p className="final-text">{finalText}</p>
            <div className="start-actions">
              <button type="button" className="btn primary" onClick={() => onGenerate(finalText)} disabled={busy || generating || !finalText.trim()}>按这个需求生成</button>
              <button type="button" className="btn ghost" onClick={restart}>重新开始</button>
            </div>
          </div>
        </section>
      )}

      <section className="start-other">
        <div>
          <h3>从示例开始</h3>
          <ul className="example-list">
            {examples.map((i) => (
              <li key={i.source + i.file}>
                <button type="button" className="starter" onClick={() => onOpen(i)}>
                  <span className="wf-name">{i.name}</span>
                  {i.intent && <span className="wf-intent">{i.intent}</span>}
                </button>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>从空白开始</h3>
          <p className="muted">自己在画布上搭：画布右上角会有一份清单，一步步告诉你接下来做什么。</p>
          <button type="button" className="btn" onClick={onBlank}>新建空白工作流</button>
        </div>
      </section>
    </div>
  );
}

function Question({ q, value, onChange }) {
  const custom = value && !q.options.includes(value);
  return (
    <fieldset className="question">
      <legend>{q.ask}</legend>
      <div className="chips">
        {q.options.map((o) => (
          <button key={o} type="button" className={`chip ${value === o ? "on" : ""}`} aria-pressed={value === o} onClick={() => onChange(value === o ? "" : o)}>{o}</button>
        ))}
        <input className="chip-input" placeholder="自己写" value={custom ? value : ""} onChange={(e) => onChange(e.target.value)} aria-label={`${q.ask}（自己写）`} />
      </div>
    </fieldset>
  );
}

function CardPart({ label, items, onChange }) {
  return (
    <div className="card-part">
      <h4>{label}</h4>
      <ul>
        {items.map((x, i) => (
          <li key={i}>
            <textarea rows={1} value={x} aria-label={`${label} 第 ${i + 1} 条`} onChange={(e) => onChange(items.map((y, j) => (j === i ? e.target.value : y)))} />
            <button type="button" className="icon-btn" title="删掉这一条" aria-label={`删掉：${x}`} onClick={() => onChange(items.filter((_, j) => j !== i))}>×</button>
          </li>
        ))}
      </ul>
      <button type="button" className="link" onClick={() => onChange([...items, ""])}>加一条</button>
    </div>
  );
}
