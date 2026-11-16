"""NetSim Tutor - Kaggle training kernel (free T4 GPU).

Pipeline:
  1. Confirm the GPU (prints nvidia-smi; expects a Tesla T4).
  2. Load the engine-grounded seed dataset (Kaggle input dataset
     `netsim-tutor-seed`: tutor_sft.train/val.jsonl + syllabus.json).
  3. (optional) Teacher distillation: a larger open instruct model, loaded in
     4-bit, generates extra networking Q&A across every syllabus topic so the
     student covers open-ended questions, not just the templated seeds.
  4. QLoRA fine-tune a small permissive student (default Qwen2.5-0.5B-Instruct).
  5. Merge the adapter to fp16.
  6. Export ONNX (int8) in the layout transformers.js expects, and optionally a
     GGUF q4 for llama.cpp/wllama.
  7. Zip everything to /kaggle/working with a manifest (version, base, sizes,
     sha256) for upload to Backblaze B2.

Everything heavy is env-configurable so the same kernel can do a fast smoke run
or a full run. It is written to fail soft: an optional stage that errors is
logged and skipped rather than killing the whole run.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
# Config (override via Kaggle kernel env / notebook edit)
# --------------------------------------------------------------------------- #
BASE_MODEL = os.environ.get("BASE_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")
# fp16 teacher that fits comfortably on a 16 GB T4 (no quantization needed).
TEACHER_MODEL = os.environ.get("TEACHER_MODEL", "Qwen/Qwen2.5-3B-Instruct")
# Production defaults = a full run. Override any of these via env for a smoke
# run (e.g. MAX_STEPS=25 TEACHER_ENABLE=0).
TEACHER_ENABLE = os.environ.get("TEACHER_ENABLE", "1") == "1"
TEACHER_PER_TOPIC = int(os.environ.get("TEACHER_PER_TOPIC", "6"))
EPOCHS = float(os.environ.get("EPOCHS", "6"))
MAX_STEPS = int(os.environ.get("MAX_STEPS", "-1"))  # -1 = use epochs
MAX_SEQ_LEN = int(os.environ.get("MAX_SEQ_LEN", "1024"))
LR = float(os.environ.get("LR", "2e-4"))
# ONNX is exported locally (ai/scripts/export_onnx.py) where the toolchain is
# controlled; Kaggle's preinstalled optimum pins an incompatible peft.
EXPORT_ONNX = os.environ.get("EXPORT_ONNX", "0") == "1"
EXPORT_GGUF = os.environ.get("EXPORT_GGUF", "0") == "1"
MODEL_VERSION = os.environ.get("MODEL_VERSION", "v2026.06.21-15b-v3")

INPUT_DIR = os.environ.get("SEED_DIR", "/kaggle/input/netsim-tutor-seed")
WORK = Path(os.environ.get("WORK_DIR", "/kaggle/working"))
ART = WORK / "artifacts"
ART.mkdir(parents=True, exist_ok=True)


def sh(cmd: str, check: bool = True) -> int:
    print(f"\n$ {cmd}", flush=True)
    return subprocess.run(cmd, shell=True, check=check).returncode


def pip_install() -> None:
    # Training stack only. optimum/onnx are installed separately right before
    # ONNX export because optimum pins an older transformers range that
    # conflicts with the trl/peft training stack; keeping them apart avoids a
    # ResolutionImpossible. Export loads the model from disk, so a later
    # transformers bump during the export install is harmless.
    pkgs = [
        "transformers==4.44.2", "datasets==2.21.0", "accelerate==0.34.2",
        "peft==0.12.0", "trl==0.9.6", "sentencepiece",
    ]
    sh("pip -q install " + " ".join(f'"{p}"' for p in pkgs), check=False)


def pip_install_export() -> None:
    sh("pip -q install optimum[onnxruntime] onnx onnxruntime", check=False)


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #

def _find(name: str):
    """Locate a seed file regardless of the exact Kaggle input mount path."""
    import glob
    direct = os.path.join(INPUT_DIR, name)
    if os.path.exists(direct):
        return direct
    hits = glob.glob(f"/kaggle/input/**/{name}", recursive=True)
    if hits:
        return hits[0]
    return None


def load_seed():
    import datasets
    print("=== /kaggle/input tree ===")
    for root, _dirs, fnames in os.walk("/kaggle/input"):
        for fn in fnames:
            print(" ", os.path.join(root, fn))
    tr = _find("tutor_sft.train.jsonl")
    va = _find("tutor_sft.val.jsonl")
    if not tr or not va:
        raise FileNotFoundError(
            "seed jsonl not found under /kaggle/input - is the "
            "netsim-tutor-seed dataset attached?")
    print("using train:", tr, "val:", va)
    ds = datasets.load_dataset("json", data_files={"train": tr, "validation": va})
    print("seed sizes:", {k: len(v) for k, v in ds.items()})
    return ds


def load_syllabus():
    p = _find("syllabus.json")
    if not p:
        return {}
    with open(p) as f:
        return json.load(f)


SYSTEM_KN = (
    "You are NetSim Tutor, an expert networking instructor covering CCNA through "
    "CCIE and real-world network/security/wireless/automation engineering."
)


def teacher_distill(syllabus: dict):
    """Use a larger model to author extra Q&A per syllabus topic."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(TEACHER_MODEL)
    model = AutoModelForCausalLM.from_pretrained(
        TEACHER_MODEL, device_map="auto", torch_dtype=torch.float16)
    model.eval()

    extra = []
    topics = [(d, t) for d, items in syllabus.items() for t in items]
    print(f"teacher distillation over {len(topics)} topics x {TEACHER_PER_TOPIC}")
    for di, (domain, topic) in enumerate(topics):
        prompt = (
            f"You are writing study Q&A for the topic '{topic}' (domain: {domain}) "
            f"in computer networking. Produce {TEACHER_PER_TOPIC} diverse "
            f"question/answer pairs ranging from basic to advanced. Each answer "
            f"must be technically correct and concise (2-5 sentences). Return ONLY "
            f"a JSON array of objects with keys 'q' and 'a'."
        )
        msgs = [{"role": "system", "content": "You are a precise networking expert."},
                {"role": "user", "content": prompt}]
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        inputs = tok(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=1200, do_sample=True,
                                 temperature=0.7, top_p=0.9)
        gen = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        try:
            start, end = gen.find("["), gen.rfind("]")
            pairs = json.loads(gen[start:end + 1])
            for pr in pairs:
                if isinstance(pr, dict) and pr.get("q") and pr.get("a"):
                    extra.append({"messages": [
                        {"role": "system", "content": SYSTEM_KN},
                        {"role": "user", "content": str(pr["q"]).strip()},
                        {"role": "assistant", "content": str(pr["a"]).strip()},
                    ], "meta": {"pillar": "knowledge", "kind": "teacher",
                                "domain": domain, "topic": topic}})
        except (json.JSONDecodeError, ValueError):
            pass
        if di % 20 == 0:
            print(f"  ...{di}/{len(topics)} topics, {len(extra)} pairs", flush=True)

    del model
    torch.cuda.empty_cache()
    out_path = ART / "teacher_qa.jsonl"
    with open(out_path, "w") as f:
        for e in extra:
            f.write(json.dumps(e) + "\n")
    print(f"teacher produced {len(extra)} Q&A -> {out_path}")
    return extra


# --------------------------------------------------------------------------- #
# Fine-tune
# --------------------------------------------------------------------------- #

def finetune(ds, extra):
    import datasets
    import torch
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    tok = AutoTokenizer.from_pretrained(BASE_MODEL)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    def to_text(ex):
        return {"text": tok.apply_chat_template(ex["messages"], tokenize=False,
                                                add_generation_prompt=False)}

    train = ds["train"]
    if extra:
        train = datasets.concatenate_datasets(
            [train, datasets.Dataset.from_list(extra)])
    train = train.map(to_text, remove_columns=train.column_names)
    val = ds["validation"].map(to_text, remove_columns=ds["validation"].column_names)
    print("final train size:", len(train))

    # fp16 LoRA (no bitsandbytes): a <=1.5B model trains comfortably in fp16 on
    # a 16 GB T4, and avoiding bnb sidesteps Kaggle's triton incompatibility.
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, device_map="auto", torch_dtype=torch.float16)
    model.config.use_cache = False
    model.enable_input_require_grads()

    lora = LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05, bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"])

    cfg = SFTConfig(
        output_dir=str(WORK / "sft"), num_train_epochs=EPOCHS,
        max_steps=MAX_STEPS, per_device_train_batch_size=2,
        gradient_accumulation_steps=8, learning_rate=LR, warmup_ratio=0.03,
        lr_scheduler_type="cosine", logging_steps=10, save_strategy="no",
        bf16=False, fp16=True, max_seq_length=MAX_SEQ_LEN, packing=True,
        dataset_text_field="text", report_to="none")

    trainer = SFTTrainer(model=model, args=cfg, train_dataset=train,
                         eval_dataset=val, peft_config=lora, tokenizer=tok)
    trainer.train()

    adapter_dir = WORK / "adapter"
    trainer.model.save_pretrained(str(adapter_dir))
    tok.save_pretrained(str(adapter_dir))
    return adapter_dir, tok


def merge(adapter_dir, tok):
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM

    base = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, torch_dtype=torch.float16, device_map="cpu")
    merged = PeftModel.from_pretrained(base, str(adapter_dir)).merge_and_unload()
    merged_dir = ART / "merged"
    merged.save_pretrained(str(merged_dir), safe_serialization=True)
    tok.save_pretrained(str(merged_dir))
    print("merged ->", merged_dir)
    return merged_dir


# --------------------------------------------------------------------------- #
# Evaluate (held-out val) -> predictions artifact for offline scoring
# --------------------------------------------------------------------------- #

def evaluate(merged_dir, ds, limit=int(os.environ.get("EVAL_LIMIT", "120"))):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

    tok = AutoTokenizer.from_pretrained(str(merged_dir))
    model = AutoModelForCausalLM.from_pretrained(
        str(merged_dir), torch_dtype=torch.float16, device_map="auto")
    gen = pipeline("text-generation", model=model, tokenizer=tok)

    rows = list(ds["validation"])[:limit]
    preds = []
    for r in rows:
        msgs = r["messages"]
        sys_user = [m for m in msgs if m["role"] in ("system", "user")]
        gold = next((m["content"] for m in msgs if m["role"] == "assistant"), "")
        prompt = tok.apply_chat_template(sys_user, tokenize=False,
                                        add_generation_prompt=True)
        out = gen(prompt, max_new_tokens=420, do_sample=False,
                  return_full_text=False)[0]["generated_text"]
        preds.append({
            "question": next((m["content"] for m in msgs if m["role"] == "user"), ""),
            "gold": gold, "pred": out.strip(),
            "pillar": (r.get("meta") or {}).get("pillar", "unknown"),
        })
    path = ART / "eval_predictions.jsonl"
    with open(path, "w") as f:
        for p in preds:
            f.write(json.dumps(p) + "\n")
    print(f"wrote {len(preds)} eval predictions -> {path}")
    del model, gen
    torch.cuda.empty_cache()
    return path


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

def export_onnx(merged_dir):
    """Export ONNX laid out exactly how transformers.js loads a model:
    config/tokenizer json at the root, ONNX weights under onnx/."""
    import shutil

    pip_install_export()
    out = ART / "onnx-web"
    raw = ART / "onnx-raw"
    rc = sh(f"optimum-cli export onnx --model {merged_dir} --task text-generation-with-past "
            f"--opset 14 {raw}", check=False)
    if rc != 0:
        print("ONNX export failed; skipping")
        return None

    onnx_sub = out / "onnx"
    onnx_sub.mkdir(parents=True, exist_ok=True)
    for p in raw.iterdir():
        if p.suffix in (".onnx", ".onnx_data") or p.name.endswith(".onnx.data"):
            shutil.move(str(p), str(onnx_sub / p.name))
        else:  # config.json, tokenizer*, special_tokens_map, generation_config
            shutil.move(str(p), str(out / p.name))
    shutil.rmtree(raw, ignore_errors=True)

    # int8 dynamic quantization -> onnx/model_quantized.onnx (used by the app
    # for both WebGPU and WASM via dtype 'q8').
    try:
        from onnxruntime.quantization import QuantType, quantize_dynamic
        src = onnx_sub / "model.onnx"
        if src.exists():
            quantize_dynamic(str(src), str(onnx_sub / "model_quantized.onnx"),
                             weight_type=QuantType.QInt8)
            print("quantized ONNX int8 -> onnx/model_quantized.onnx")
    except Exception as exc:  # noqa: BLE001 - optional stage
        print("onnx quantize skipped:", exc)
    return out


def export_gguf(merged_dir):
    sh("git clone --depth 1 https://github.com/ggerganov/llama.cpp /kaggle/working/llama.cpp",
       check=False)
    sh("pip -q install -r /kaggle/working/llama.cpp/requirements.txt", check=False)
    f16 = ART / "tutor-f16.gguf"
    rc = sh(f"python /kaggle/working/llama.cpp/convert_hf_to_gguf.py {merged_dir} "
            f"--outfile {f16} --outtype f16", check=False)
    if rc != 0:
        print("GGUF convert failed; skipping")
        return None
    sh("cmake -B /kaggle/working/llama.cpp/build /kaggle/working/llama.cpp "
       "-DLLAMA_CURL=OFF && cmake --build /kaggle/working/llama.cpp/build "
       "--target llama-quantize -j", check=False)
    q4 = ART / "tutor-q4_k_m.gguf"
    sh(f"/kaggle/working/llama.cpp/build/bin/llama-quantize {f16} {q4} Q4_K_M",
       check=False)
    # Shrink the kernel output: drop the cloned/built llama.cpp tree and the
    # large f16 gguf once the q4 quant exists (q4 is the browser artifact).
    sh("rm -rf /kaggle/working/llama.cpp", check=False)
    if q4.exists() and f16.exists():
        f16.unlink()
    return q4 if q4.exists() else f16


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_and_zip():
    files = []
    for p in ART.rglob("*"):
        if p.is_file():
            files.append({"path": str(p.relative_to(ART)),
                          "bytes": p.stat().st_size, "sha256": _sha(p)})
    man = {"name": "netsim-tutor", "version": MODEL_VERSION,
           "base_model": BASE_MODEL, "teacher": TEACHER_MODEL if TEACHER_ENABLE else None,
           "created": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "files": files}
    with open(ART / "manifest.json", "w") as f:
        json.dump(man, f, indent=2)
    sh(f"cd {ART} && zip -r {WORK}/netsim-tutor-{MODEL_VERSION}.zip .", check=False)
    print(json.dumps(man, indent=2)[:2000])


def main():
    sh("nvidia-smi", check=False)
    pip_install()
    ds = load_seed()
    syllabus = load_syllabus()
    extra = []
    if TEACHER_ENABLE and syllabus:
        try:
            extra = teacher_distill(syllabus)
        except Exception as exc:  # noqa: BLE001 - optional stage
            print("teacher distillation failed; continuing with seed only:", exc)
    adapter_dir, tok = finetune(ds, extra)
    merged_dir = merge(adapter_dir, tok)
    try:
        evaluate(merged_dir, ds)
    except Exception as exc:  # noqa: BLE001 - optional stage
        print("eval skipped:", exc)
    if EXPORT_ONNX:
        export_onnx(merged_dir)
    if EXPORT_GGUF:
        export_gguf(merged_dir)
    manifest_and_zip()
    print("DONE. Artifacts in", ART)


if __name__ == "__main__":
    sys.exit(main())
