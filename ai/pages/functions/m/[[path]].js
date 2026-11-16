// Cloudflare Pages Function: free CDN proxy in front of the public Backblaze B2
// bucket that stores NetSim tutor model artifacts.
//
// Route:  https://<project>.pages.dev/m/<file>   ->   B2 /file/netsim-models/netsim-tutor/<file>
//
// Why a proxy and not a direct B2 link:
//  * Free egress: B2 <-> Cloudflare are in the Bandwidth Alliance, so bytes
//    served through Cloudflare's edge cost nothing.
//  * CORS: the browser fetches weights cross-origin; we add permissive CORS.
//  * Caching: weights are immutable per version, so we cache hard at the edge.

const B2_BASE = "https://f005.backblazeb2.com/file/netsim-models/netsim-tutor";
// Bumping this changes the upstream cache key, which evicts any stale entry
// (e.g. a 404 cached before the file was uploaded).
const CACHE_VERSION = "3";

export async function onRequest(context) {
  const { request, params } = context;
  const parts = Array.isArray(params.path) ? params.path : [params.path];
  const objectPath = parts.join("/");

  if (request.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders() });
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("Method Not Allowed", { status: 405, headers: corsHeaders() });
  }

  const upstreamUrl = `${B2_BASE}/${objectPath}`;
  const fetchUrl = `${upstreamUrl}?cv=${CACHE_VERSION}`;
  const cache = caches.default;
  const cacheKey = new Request(fetchUrl, { method: "GET" });

  let response = await cache.match(cacheKey);
  if (!response) {
    const upstream = await fetch(fetchUrl, {
      // forward Range so large weights can be fetched in chunks
      headers: rangeHeaders(request),
      // Only cache success long-term; never cache an error (a missing file
      // that gets uploaded later must not be pinned as a 404).
      cf: {
        cacheEverything: true,
        cacheTtlByStatus: { "200-299": 31536000, "300-399": 0, "400-599": 0 },
      },
    });
    response = new Response(upstream.body, upstream);
    const partial = upstream.status === 206;
    response.headers.set(
      "Cache-Control",
      upstream.status === 200 || partial
        ? "public, max-age=31536000, immutable"
        : "no-store");
    if (upstream.status === 200) {
      context.waitUntil(cache.put(cacheKey, response.clone()));
    }
  }

  const out = new Response(response.body, response);
  const cors = corsHeaders();
  for (const [k, v] of Object.entries(cors)) out.headers.set(k, v);
  return out;
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "Range, Content-Type",
    "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, ETag",
    "Cross-Origin-Resource-Policy": "cross-origin",
  };
}

function rangeHeaders(request) {
  const h = {};
  const range = request.headers.get("Range");
  if (range) h["Range"] = range;
  return h;
}
