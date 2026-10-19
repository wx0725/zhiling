import { useEffect, useRef, useState } from "react";

// 左侧项目栏：每个项目是一个独立的工作区，记着里面正在做的工作流。
// 点一下切换；“⋯”里可以重命名、删除；双击名字也能重命名
export function ProjectList({ projects, active, busy, onNew, onOpen, onRename, onDelete }) {
  const [editing, setEditing] = useState(null);
  const [menu, setMenu] = useState(null);
  const menuRef = useRef(null);
  useEffect(() => {
    if (!menu) return;
    const close = (e) => { if (!menuRef.current?.contains(e.target)) setMenu(null); };
    const esc = (e) => { if (e.key === "Escape") setMenu(null); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [menu]);

  return (
    <div className="group projects">
      <div className="group-head">
        <h2>项目</h2>
        <button type="button" className="icon-btn group-add" onClick={onNew} title="新建项目" aria-label="新建项目">
          <svg viewBox="0 0 20 20" aria-hidden="true" className="ico"><path d="M10 4.5v11M4.5 10h11" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
        </button>
      </div>
      {projects.length === 0
        ? <p className="side-empty">还没有项目。点右上角的“+”新建一个，或者直接在上面输入需求、打开下面的示例。</p>
        : (
          <ul>
            {projects.map((p) => (
              <li key={p.id} className={`proj-row ${p.id === active ? "on" : ""}`} ref={menu === p.id ? menuRef : undefined}>
                {editing === p.id
                  ? <RenameField name={p.name} onDone={(v) => { setEditing(null); if (v && v !== p.name) onRename(p.id, v); }} />
                  : (
                    <button type="button" className="proj-item" aria-current={p.id === active ? "page" : undefined} title={p.name}
                            onClick={() => onOpen(p.id)} onDoubleClick={() => setEditing(p.id)}>
                      <span className="proj-name">{p.name}</span>
                      {busy === p.id && <span className="proj-dot" title="正在生成" aria-label="正在生成" />}
                    </button>
                  )}
                {editing !== p.id && (
                  <button type="button" className="proj-more" aria-label={`“${p.name}”的更多操作`} title="更多操作"
                          aria-haspopup="menu" aria-expanded={menu === p.id} onClick={() => setMenu(menu === p.id ? null : p.id)}>
                    <svg viewBox="0 0 20 20" aria-hidden="true" className="ico"><circle cx="5" cy="10" r="1.4" fill="currentColor" /><circle cx="10" cy="10" r="1.4" fill="currentColor" /><circle cx="15" cy="10" r="1.4" fill="currentColor" /></svg>
                  </button>
                )}
                {menu === p.id && (
                  <div className="menu proj-menu" role="menu">
                    <button type="button" role="menuitem" className="menu-item" onClick={() => { setMenu(null); setEditing(p.id); }}>重命名</button>
                    <button type="button" role="menuitem" className="menu-item danger-item" onClick={() => { setMenu(null); onDelete(p); }}>删除</button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
    </div>
  );
}

// 就地改名：回车或点别处确认，Esc 放弃
function RenameField({ name, onDone }) {
  const [v, setV] = useState(name);
  const ref = useRef(null);
  const done = useRef(false);
  useEffect(() => { ref.current?.focus(); ref.current?.select(); }, []);
  const finish = (value) => {
    if (done.current) return;
    done.current = true;
    onDone(value);
  };
  return (
    <input ref={ref} className="input proj-rename" value={v} maxLength={80} aria-label="项目名"
           onChange={(e) => setV(e.target.value)} onBlur={() => finish(v.trim())}
           onKeyDown={(e) => {
             if (e.key === "Enter") { e.preventDefault(); finish(v.trim()); }
             else if (e.key === "Escape") { e.preventDefault(); finish(null); }
           }} />
  );
}
