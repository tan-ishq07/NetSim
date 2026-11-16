# NetSim Tutor AI pipeline

A fully-free pipeline that trains a small, networking-focused tutor model and
serves it to every user's browser at no per-query cost.

```
 app/tutor_ai/  ──gen──▶  ai/data/*.jsonl ──Kaggle dataset──▶  ai/kaggle/train_tutor.py
                                                                      │ (free T4 GPU)
                                                                      ▼
                                            merged model + ONNX(int8) + GGUF(q4) + manifest
                                                                      │ ai/scripts/upload_artifacts.py
                                                                      ▼
                                              Backblaze B2 (public)  ◀── ai/pages (Cloudflare Pages proxy)
                                                                      │  free egress + CORS + edge cache
                                                                      ▼
                                          browser: app/static/tutor_ai.js (transformers.js, WebGPU/WASM)
```

## 1. Dataset (`app/tutor_ai/`, output to `ai/data/`)

Three grounded pillars (regenerate with `python -m app.tutor_ai.generate`):

| pillar | how it's built | grounding |
| --- | --- | --- |
| diagnosis | inject one fault into each working lab, observe real ping/analyzer symptoms, label the exact IOS fix | the simulator is the oracle — tests prove each fault breaks connectivity and each fix restores it |
| cli | walk NetSim's own `?` grammar (`clihelp.SPEC`) | answers match the live console exactly |
| knowledge | authored concept Q&A + procedurally-computed subnetting + well-known ports, over a 123-topic syllabus | subnetting verified arithmetically |

## 2. Training (`ai/kaggle/train_tutor.py`)

Runs as a Kaggle script kernel on a free T4:

1. (optional) teacher distillation — an fp16 instruct model authors extra Q&A across every syllabus topic so the student answers open-ended questions.
2. fp16 **LoRA** fine-tune of `Qwen/Qwen2.5-0.5B-Instruct` (Apache-2.0, browser-sized). No bitsandbytes — sidesteps Kaggle's triton incompatibility and is unnecessary at this size.
3. merge the adapter to fp16.
4. held-out eval → `eval_predictions.jsonl` (scored offline by `ai/eval/score.py`).
5. export **ONNX int8** in transformers.js layout (`onnx/model_quantized.onnx` + configs at root) and **GGUF q4** (llama.cpp).
6. zip artifacts + `manifest.json` (version, base, sizes, sha256).

Push/run:

```bash
export KAGGLE_API_TOKEN=...            # account with free GPU quota
kaggle datasets create -p ai/data      # uploads tutor_sft.*.jsonl + syllabus.json
kaggle kernels push -p ai/kaggle       # runs on T4; poll: kaggle kernels status <id>
kaggle kernels output <id> -p ./out    # download the artifacts zip
```

## 3. Delivery (`ai/pages/`, `ai/scripts/upload_artifacts.py`)

```bash
export B2_KEY_ID=... B2_APP_KEY=...
python ai/scripts/upload_artifacts.py --src ./out/artifacts/onnx-web --version v2026.06.19 --latest
# Cloudflare Pages proxy (free *.pages.dev, free B2 egress via Bandwidth Alliance):
cd ai/pages && wrangler pages deploy public --project-name netsim-tutor --branch main
```

Model files end up at `https://netsim-tutor.pages.dev/m/latest/...` with CORS +
immutable edge caching.

## 4. In-browser inference (`app/static/tutor_ai.js`)

transformers.js loads the ONNX model from the Pages CDN, runs it on WebGPU
(WASM fallback), and caches it in the browser after first download. The Tutor
tab uses a 4-tier fallback so it always answers:

1. on-device model, **grounded** with the server's live topology scan (`/api/tutor` findings)
2. Cloudflare Workers AI (if configured server-side)
3. deterministic rules engine (always available)

## 5. Eval (`ai/eval/score.py`)

Pillar-aware, GPU-free scorer. Headline metric is **key-token recall** (did the
prediction contain the load-bearing commands/IPs/facts from the gold answer),
plus exact-match, broken down per pillar.

```bash
python ai/eval/score.py --pred eval_predictions.jsonl
```
