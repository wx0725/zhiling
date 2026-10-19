from core.text import to_text


def run(inputs: dict) -> dict:
    parts = [to_text(v) for v in inputs["items"]]
    if inputs["numbered"]:
        parts = [f"{i}. {p}" for i, p in enumerate(parts, 1)]
    return {"text": inputs["separator"].join(parts)}
