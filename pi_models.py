"""Generate Pi's models.json from every MLX chat model in the model library.

Usage: python3 pi_models.py MODELS_ROOT DEFAULT_MODEL_DIR > models.json
       python3 pi_models.py MODELS_ROOT DEFAULT_MODEL_DIR --list   (human summary)
       python3 pi_models.py MODELS_ROOT DEFAULT_MODEL_DIR --pick   (dir<TAB>label, default first; for hush's start-up menu)

A model is offered if its folder has config.json + *.safetensors and a chat
template that supports tools (Pi is an agent; without tool calling it can't
work). Thinking support is detected from the template (enable_thinking).
GGUF folders are skipped (llama.cpp format; MLX can't load them).
"""
import json
import sys
from pathlib import Path

# Labels/notes for models already tested in this setup (folder name -> note).
KNOWN = {
    "Qwen3.6-35B-A3B-MLX-8bit": "MoE, strongest, best at spotting names",
    "Qwen3-Coder-30B-A3B-Instruct-MLX-6bit": "MoE, fastest coder, ~45-50 tok/s",
    "Qwen3.8-27B-MLX-6bit": "dense, slow ~9 tok/s, careful",
    "Qwen3.8-27B-MLX-8bit": "dense, slow, careful",
    "NVIDIA-Nemotron-3-Nano-30B-A3B-MLX-8bit": "MoE, ~50 tok/s, over-flags",
    "gpt-oss-20b-MXFP4-Q8": "MoE, ~75 tok/s",
    "gpt-oss-20b-MLX-8bit": "MoE, ~50 tok/s",
    "LFM2-24B-A2B-MLX-8bit": "MoE, ~80 tok/s",
    "gemma-4-26B-A4B-it-QAT-MLX-4bit": "MoE, ~60 tok/s, cleanest name-spotting, thinking loops",
    "gemma-4-E4B-it-MLX-6bit": "small",
    "Devstral-Small-2-24B-Instruct-2512-4bit": "dense",
    "Magistral-Small-2509-MLX-8bit": "dense, garbled output in testing",
}
SKIP_TYPES = {"bert", "nomic_bert", "whisper", "clip", "siglip"}
CONTEXT = 65536
ONOFF = {"minimal": None, "low": None, "medium": None, "xhigh": None}


def template(d):
    t = ""
    if (d / "chat_template.jinja").exists():
        t += (d / "chat_template.jinja").read_text(errors="ignore")
    tc = d / "tokenizer_config.json"
    if tc.exists():
        try:
            ct = json.loads(tc.read_text()).get("chat_template") or ""
            t += ct if isinstance(ct, str) else json.dumps(ct)
        except ValueError:
            pass
    return t


def scan(root):
    found, skipped = [], []
    for d in sorted(p for p in Path(root).glob("*/*") if p.is_dir()):
        weights = list(d.glob("*.safetensors"))
        if not (d / "config.json").exists() or not weights:
            if list(d.glob("*.gguf")):
                skipped.append((d.name, "GGUF (llama.cpp only)"))
            continue
        cfg = json.loads((d / "config.json").read_text())
        tcfg = cfg.get("text_config", cfg)
        mtype = cfg.get("model_type", "?")
        t = template(d)
        if mtype in SKIP_TYPES or not t:
            skipped.append((d.name, "not a chat model"))
            continue
        if "tools" not in t:
            skipped.append((d.name, "no tool calling in chat template"))
            continue
        experts = tcfg.get("num_experts") or tcfg.get("num_local_experts") or tcfg.get("n_routed_experts")
        found.append({
            "dir": d,
            "gb": sum(w.stat().st_size for w in weights) / 2**30,
            "moe": bool(experts),
            "think": "enable_thinking" in t,
            "ctx": min(CONTEXT, int(tcfg.get("max_position_embeddings") or CONTEXT)),
        })
    return found, skipped


def entry(m, default=False):
    note = KNOWN.get(m["dir"].name, "untested here")
    kind = "MoE" if m["moe"] else "dense"
    if kind not in note:
        note = f"{kind}, {note}"
    name = f"{m['dir'].name} ({note}, {m['gb']:.0f} GB)"
    e = {
        "id": "default_model" if default else str(m["dir"]),
        "name": ("Default: " if default else "") + name,
        "reasoning": m["think"],
        "contextWindow": m["ctx"],
        "maxTokens": 16384,
    }
    if m["think"]:
        e["thinkingLevelMap"] = ONOFF
    return e


def main():
    root, default_dir = sys.argv[1], Path(sys.argv[2]).resolve()
    found, skipped = scan(root)
    default = [m for m in found if m["dir"].resolve() == default_dir]
    rest = [m for m in found if m["dir"].resolve() != default_dir]
    models = [entry(m, True) for m in default] + [entry(m) for m in rest]
    if "--pick" in sys.argv:
        for m, e in zip(default + rest, models):
            print(f"{m['dir']}\t{e['name']}{'  [thinking]' if e['reasoning'] else ''}")
        return
    if "--list" in sys.argv:
        for e in models:
            print(f"  {'*' if e['id'] == 'default_model' else ' '} {e['name']}{'  [thinking]' if e['reasoning'] else ''}")
        for n, why in skipped:
            print(f"    skipped {n}: {why}")
        return
    print(json.dumps({"providers": {"mlx": {
        "baseUrl": "http://host.docker.internal:8080/v1",
        "api": "openai-completions",
        "apiKey": "not-needed",
        "compat": {
            "supportsDeveloperRole": False,
            "supportsReasoningEffort": False,
            "supportsStore": False,
            "maxTokensField": "max_tokens",
            "thinkingFormat": "qwen-chat-template",
        },
        "models": models,
    }}}, indent=2))


if __name__ == "__main__":
    main()
