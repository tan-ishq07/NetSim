// On-device NetSim tutor: runs a small fine-tuned model in the browser via
// transformers.js (WebGPU, WASM fallback). The model files are served from our
// free Cloudflare Pages CDN (which proxies the public Backblaze B2 bucket) and
// cached by the browser after first load. Grounding facts (analyzer findings +
// topology) are supplied by the server's /api/tutor so answers stay correct.
//
// Exposes window.NetSimTutorAI with:
//   isSupported()            -> bool (WebGPU or WASM available)
//   state()                  -> 'idle' | 'loading' | 'ready' | 'error'
//   load(onProgress)         -> Promise (downloads + warms the model)
//   answer(messages)         -> Promise<string>
//   backend                  -> 'webgpu' | 'wasm' | null (set after load)

(function () {
  const CDN = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.0.2";
  // Model files live at https://netsim-tutor.pages.dev/m/<MODEL>/... (served
  // from the public B2 bucket via a Cloudflare Pages Function). We pin an
  // explicit, immutable version rather than "latest" so the CDN can cache it
  // forever and a new release is a one-line bump here.
  const MODEL_HOST = "https://netsim-tutor.pages.dev/m/";
  // On-device model is chosen per backend. The fine-tuned Qwen2.5-1.5B (4-bit,
  // ~1.36GB) is a big quality jump but its session needs ~3GB of memory to
  // initialise, which overflows the 32-bit WASM heap (~4GB hard cap, OOMs in
  // practice). It runs only on WebGPU, where weights live in GPU memory. The
  // WASM fallback keeps the proven 0.5B (q8, ~0.5GB) so on-device still works
  // everywhere. Both paths are single-file ONNX (no external-data sidecar).
  const MODEL_WEBGPU = "v2026.06.21-15b-v3-q4"; // Qwen2.5-1.5B, 4-bit (v3)
  const MODEL_WASM = "v2026.06.19";             // Qwen2.5-0.5B, q8
  let MODEL = MODEL_WASM;

  let _state = "idle";
  let _generator = null;
  let _lib = null;
  let _backend = null;

  function isSupported() {
    return typeof navigator !== "undefined";
  }

  async function _loadLib() {
    if (_lib) return _lib;
    _lib = await import(/* webpackIgnore: true */ CDN);
    return _lib;
  }

  async function _pickDevice() {
    if (navigator.gpu) {
      try {
        const adapter = await navigator.gpu.requestAdapter();
        if (adapter) return "webgpu";
      } catch (_e) { /* fall through */ }
    }
    return "wasm";
  }

  async function load(onProgress) {
    if (_state === "ready") return;
    _state = "loading";
    try {
      const { pipeline, env } = await _loadLib();
      env.allowLocalModels = false;
      env.useBrowserCache = true;
      // Point transformers.js at our CDN instead of huggingface.co.
      env.remoteHost = MODEL_HOST;
      env.remotePathTemplate = "{model}";

      const device = await _pickDevice();
      _backend = device;
      MODEL = device === "webgpu" ? MODEL_WEBGPU : MODEL_WASM;
      const dtype = device === "webgpu" ? "q4" : "q8";
      _generator = await pipeline("text-generation", MODEL, {
        device,
        dtype,
        progress_callback: (p) => {
          if (typeof onProgress === "function") onProgress(p);
        },
      });
      _state = "ready";
    } catch (e) {
      _state = "error";
      throw e;
    }
  }

  async function answer(messages) {
    if (_state !== "ready" || !_generator) {
      throw new Error("model not loaded");
    }
    const out = await _generator(messages, {
      max_new_tokens: 320,
      do_sample: false,
      temperature: 0,
      return_full_text: false,
    });
    // transformers.js returns the chat with the appended assistant turn.
    const gen = out && out[0] ? out[0].generated_text : null;
    if (Array.isArray(gen)) {
      const last = gen[gen.length - 1];
      return (last && last.content ? last.content : "").trim();
    }
    return (typeof gen === "string" ? gen : "").trim();
  }

  const api = {
    isSupported,
    state: () => _state,
    load,
    answer,
    get backend() { return _backend; },
    MODEL_HOST,
    get MODEL() { return MODEL; },
  };
  window.NetSimTutorAI = api;
})();
