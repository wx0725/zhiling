// 运行表单：初始值与提交值。纯函数，便于单独测试。

const isOptional = (p) => p.required === false || "default" in p;

// 只有这些类型的输入框里是原样文字；其余（any、列表、object、数字）按 JSON 填写，与 parseValue 互为逆操作
const isText = (type) => type === "string" || type === "file";

// 表单里显示的文字（布尔值是勾选状态）
export function toRaw(type, v) {
  if (type === "boolean") return !!v;
  if (isText(type)) return String(v);
  return JSON.stringify(v);
}

// 有声明默认值的用默认值；可选又没默认值的留空（提交时不发送，交给后端按“没有提供”处理）；必填的给一个示例
function example(name, type) {
  const n = name.toLowerCase();
  if (type === "string" && n.includes("url")) return "https://example.com";
  if (type === "list<string>" && n.includes("url")) return '["https://example.com", "https://example.com/no-such-page"]';
  if (type === "integer" || type === "number") return "85";
  if (type === "boolean") return false;
  if (type.startsWith("list<")) return "[]";
  return "";
}

export function initialValues(inputs) {
  return Object.fromEntries(Object.entries(inputs || {}).map(([k, p]) => {
    if ("default" in p && p.default !== null) return [k, toRaw(p.type, p.default)];
    if (isOptional(p)) return [k, p.type === "boolean" ? null : ""]; // null：未选择
    return [k, example(k, p.type)];
  }));
}

export function parseValue(type, raw) {
  if (type === "boolean") return !!raw;
  if (type === "integer") {
    if (!/^-?\d+$/.test(String(raw).trim())) throw new Error("需要整数");
    return Number(raw);
  }
  if (type === "number") {
    const v = Number(raw);
    if (String(raw).trim() === "" || Number.isNaN(v)) throw new Error("需要数字");
    return v;
  }
  if (type.startsWith("list<") || type === "object" || type === "any") {
    try {
      return JSON.parse(raw);
    } catch {
      throw new Error(type.startsWith("list<") ? '需要 JSON 列表，例如 ["a", "b"]' : "需要 JSON");
    }
  }
  return raw;
}

// 返回 {values, errors}；可选输入留空（或布尔未选择）就不发送，后端会补默认值或按“没有提供”处理
export function collectInputs(inputs, raw) {
  const values = {};
  const errors = {};
  for (const [k, p] of Object.entries(inputs || {})) {
    const r = raw[k];
    if (isOptional(p) && (r === null || r === undefined || (p.type !== "boolean" && String(r).trim() === ""))) continue;
    try {
      values[k] = parseValue(p.type, r);
    } catch (err) {
      errors[k] = err.message;
    }
  }
  return { values, errors };
}
