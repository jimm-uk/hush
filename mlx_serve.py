"""mlx_lm.server with sane memory behaviour for a shared Mac.

mlx_lm.server wires up to the GPU's max recommended working set (~52 GB on a
64 GB Mac) and leaves MLX's buffer pool uncapped, so freed scratch memory from
long prefills is kept forever. This wrapper:

  * caps the buffer pool (MLX_CACHE_LIMIT, default 2GB): freed buffers beyond
    that go back to macOS
  * caps wired memory (MLX_WIRED_LIMIT, default: weights + 2 GB)
  * logs active / pool / peak memory every MLX_MEM_LOG seconds (0 = off)

All other arguments go straight to mlx_lm.server.
"""
import os
import sys
import threading
import time
from pathlib import Path

import mlx.core as mx
from mlx_lm import server
from mlx_lm.utils import _parse_size

GB = 1 << 30


def model_bytes(argv):
    try:
        d = Path(argv[argv.index("--model") + 1])
        return sum(f.stat().st_size for f in d.glob("*.safetensors"))
    except (ValueError, IndexError, OSError):
        return 0


cache_limit = _parse_size(os.environ.get("MLX_CACHE_LIMIT", "2GB"))
wired_env = os.environ.get("MLX_WIRED_LIMIT")
wired_limit = _parse_size(wired_env) if wired_env else model_bytes(sys.argv) + 2 * GB

mx.set_cache_limit(cache_limit)

# server.main() calls mx.set_wired_limit(max_recommended); substitute ours.
_set_wired = mx.set_wired_limit
mx.set_wired_limit = lambda _requested: _set_wired(wired_limit)


# On a model switch, free the old model BEFORE loading the new one. Otherwise
# both are briefly resident (seen: 44 GB peak swapping a 21 GB for a 23 GB model).
import gc

_orig_load = server.ModelProvider._load


def _load_freeing_first(self, *args, **kwargs):
    # The server's generate loop still holds the old model in a local variable
    # while loading, so dropping our reference isn't enough: empty the old
    # model's weight arrays in place.
    from mlx.utils import tree_map

    for old in (self.model, self.draft_model):
        if old is not None:
            old.update(tree_map(lambda _: mx.zeros((0,)), old.parameters()))
    self.model = self.tokenizer = self.draft_model = None
    self.model_key = None
    gc.collect()
    mx.clear_cache()
    return _orig_load(self, *args, **kwargs)


server.ModelProvider._load = _load_freeing_first


# Sampling defaults per model. mlx_lm.server falls back to temp 0 (greedy) when a
# client sends no sampling params, which Pi doesn't. Greedy decoding makes some
# models loop (Gemma 4 re-counted until max_tokens). Use the model's own
# generation_config.json values for any params the request leaves out.
import json as _json

_gen_cfg_cache = {}


def _gen_cfg(model_dir):
    if model_dir not in _gen_cfg_cache:
        try:
            c = _json.loads((Path(model_dir) / "generation_config.json").read_text())
            _gen_cfg_cache[model_dir] = c if c.get("do_sample", True) else {}
        except (OSError, ValueError, TypeError):
            _gen_cfg_cache[model_dir] = {}
    return _gen_cfg_cache[model_dir]


_orig_validate = server.APIHandler.validate_model_parameters


def _validate_with_model_sampling(self):
    model = self.requested_model
    if model == "default_model":
        model = self.response_generator.cli_args.model
    cfg = _gen_cfg(model)
    applied = {}
    for body_key, cfg_key, attr in (("temperature", "temperature", "temperature"),
                                    ("top_p", "top_p", "top_p"),
                                    ("top_k", "top_k", "top_k"),
                                    ("min_p", "min_p", "min_p")):
        if body_key not in self.body and cfg.get(cfg_key) is not None:
            setattr(self, attr, cfg[cfg_key])
            applied[attr] = cfg[cfg_key]
    if applied:
        print(f"[sampling] {Path(str(model)).name}: model defaults {applied}", file=sys.stderr, flush=True)
    return _orig_validate(self)


server.APIHandler.validate_model_parameters = _validate_with_model_sampling


def log_memory(every):
    while True:
        time.sleep(every)
        print(
            f"[mem] active {mx.get_active_memory() / GB:.1f} GB · "
            f"pool {mx.get_cache_memory() / GB:.1f} GB · "
            f"peak {mx.get_peak_memory() / GB:.1f} GB",
            file=sys.stderr,
            flush=True,
        )


every = int(os.environ.get("MLX_MEM_LOG", "60"))
if every > 0:
    threading.Thread(target=log_memory, args=(every,), daemon=True).start()

print(
    f"[mem] cache limit {cache_limit / GB:.1f} GB · wired limit {wired_limit / GB:.1f} GB",
    file=sys.stderr,
    flush=True,
)
sys.argv[0] = "mlx_lm.server"
server.main()
