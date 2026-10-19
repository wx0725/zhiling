// 给使用者看的类型名称：界面上不直接显示 integer、list<string> 这类程序写法（悬停时仍可看到原写法）。

const BASE = {
  string: "文字",
  integer: "整数",
  number: "数字",
  boolean: "是或否",
  object: "对象",
  file: "文件",
  any: "任意",
};

export function typeLabel(t) {
  if (typeof t !== "string") return "任意";
  const m = t.match(/^list<(.+)>$/);
  if (m) return `${typeLabel(m[1])}列表`;
  return BASE[t] || t;
}
