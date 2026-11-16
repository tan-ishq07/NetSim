"""Export a merged HF model to the ONNX layout transformers.js expects.

Run locally (CPU is fine for <=1.5B) against the merged model downloaded from
the Kaggle training kernel. Produces:

    <out>/config.json, generation_config.json, tokenizer*.json, ...
    <out>/onnx/model.onnx
    <out>/onnx/model_quantized.onnx   (int8, used by the app for WebGPU + WASM)

This is kept off Kaggle on purpose: Kaggle's preinstalled optimum pins a newer
peft than the training stack, which breaks the in-kernel export. Doing it in a
controlled local env is reliable and reproducible.

Setup (one-time, isolated):
    python -m venv ~/.netsim_onnx_env
    ~/.netsim_onnx_env/bin/pip install "optimum[onnxruntime]==1.21.4" \
        "transformers==4.44.2" onnx onnxruntime
    ~/.netsim_onnx_env/bin/python ai/scripts/export_onnx.py --model <merged_dir> --out <out>
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="merged HF model dir")
    ap.add_argument("--out", required=True, help="output dir (transformers.js layout)")
    ap.add_argument("--opset", type=int, default=14)
    args = ap.parse_args()

    out = Path(args.out)
    raw = out.parent / (out.name + "-raw")
    if raw.exists():
        shutil.rmtree(raw)
    out.mkdir(parents=True, exist_ok=True)

    rc = subprocess.run(
        [sys.executable, "-m", "optimum.commands.optimum_cli", "export", "onnx",
         "--model", args.model, "--task", "text-generation-with-past",
         "--opset", str(args.opset), str(raw)],
        check=False).returncode
    if rc != 0:
        # fall back to the console entrypoint
        rc = subprocess.run(
            ["optimum-cli", "export", "onnx", "--model", args.model,
             "--task", "text-generation-with-past", "--opset", str(args.opset),
             str(raw)], check=False).returncode
    if rc != 0:
        sys.exit("optimum-cli export failed")

    onnx_sub = out / "onnx"
    onnx_sub.mkdir(parents=True, exist_ok=True)
    for p in raw.iterdir():
        if p.suffix in (".onnx", ".onnx_data") or p.name.endswith(".onnx.data"):
            shutil.move(str(p), str(onnx_sub / p.name))
        else:
            shutil.move(str(p), str(out / p.name))
    shutil.rmtree(raw, ignore_errors=True)

    try:
        from onnxruntime.quantization import QuantType, quantize_dynamic
        src = onnx_sub / "model.onnx"
        if src.exists():
            quantize_dynamic(str(src), str(onnx_sub / "model_quantized.onnx"),
                             weight_type=QuantType.QInt8)
            print("quantized -> onnx/model_quantized.onnx")
    except Exception as exc:  # noqa: BLE001 - optional
        print("quantize skipped:", exc)

    print("ONNX export complete ->", out)
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(out)}  {p.stat().st_size:,} B")


if __name__ == "__main__":
    main()
