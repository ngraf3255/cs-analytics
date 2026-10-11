// Worker in front of the static assets. Real files (JS/CSS/SVG…) are served by the assets layer before
// this runs; everything else lands here. Known SPA routes get index.html (200); anything else gets the
// same index.html with a real 404 status (the app renders "Not found · ← Home" for unknown paths).
// Keep in sync with src/steam/routes.ts.
const SPA_ROUTES = new Set(["/", "/stats", "/about", "/account", "/account/export", "/predict"]);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    const index = await env.ASSETS.fetch(new Request(new URL("/", url), { headers: request.headers }));
    if (SPA_ROUTES.has(path)) return index;
    return new Response(index.body, { status: 404, headers: index.headers });
  },
};
