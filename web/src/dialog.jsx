// 屏幕中央的确认框，代替浏览器自带的 window.confirm（那个只能出现在页面顶部，样式也改不了）。
// 用法：const [ask, dialog] = useConfirm(); 在页面里放 {dialog}；需要确认时 if (await ask({...})) ...
// 打开时按 Esc 或点遮罩外面算取消，按 Enter 算确定；焦点先落在“确定”上。同一时间只有一个确认框，新的会把旧的当作取消。
import { useCallback, useEffect, useRef, useState } from "react";

export function useConfirm() {
  const [req, setReq] = useState(null); // { title, message, ok, cancel, danger, resolve }
  const ask = useCallback((opts) => new Promise((resolve) => {
    setReq((old) => {
      old?.resolve(false);
      return { title: "请确认", ok: "确定", cancel: "取消", ...opts, resolve };
    });
  }), []);
  const close = (answer) => {
    req?.resolve(answer);
    setReq(null);
  };
  return [ask, req ? <ConfirmDialog req={req} onClose={close} /> : null];
}

function ConfirmDialog({ req, onClose }) {
  const okRef = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  // 只在这个确认框打开时把焦点放到“确定”上一次；页面别处重画不会把使用者移走的焦点抢回来
  useEffect(() => {
    okRef.current?.focus();
    const key = (e) => {
      if (e.key === "Escape") { e.preventDefault(); closeRef.current(false); }
    };
    window.addEventListener("keydown", key, true);
    return () => window.removeEventListener("keydown", key, true);
  }, [req]);
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(false); }}>
      <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="dialog-title" aria-describedby="dialog-message">
        <h2 id="dialog-title">{req.title}</h2>
        <p id="dialog-message">{req.message}</p>
        <div className="dialog-actions">
          <button type="button" className="btn ghost" onClick={() => onClose(false)}>{req.cancel}</button>
          <button type="button" ref={okRef} className={`btn ${req.danger ? "danger" : "primary"}`} onClick={() => onClose(true)}>{req.ok}</button>
        </div>
      </div>
    </div>
  );
}
