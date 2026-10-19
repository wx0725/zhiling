from core.errors import ModuleError


def run(inputs: dict) -> dict:
    items, keep = inputs["items"], inputs["keep"]
    if len(items) != len(keep):
        raise ModuleError("length_mismatch", f"items 有 {len(items)} 项，keep 有 {len(keep)} 项，必须一样多")
    kept = [v for v, k in zip(items, keep) if k]
    return {"items": kept, "count": len(kept)}
