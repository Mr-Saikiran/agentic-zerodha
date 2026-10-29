// Forwards techswitch.in/equity/* to the FastAPI origin and adds the shared origin secret.
// The rest of techswitch.in is untouched. Cloudflare Access should protect /equity* in front of this.

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const target = new URL(url.pathname + url.search, env.ORIGIN_URL);

    const headers = new Headers(request.headers);
    headers.set("X-Origin-Auth", env.ORIGIN_SECRET);
    headers.set("X-Forwarded-Host", url.host);
    headers.set("X-Forwarded-Proto", "https");

    // Pass the response through as-is: SSE chat streams aren't buffered, and redirects reach the browser.
    return fetch(target, {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
      redirect: "manual",
    });
  },

  // Cron trigger (see wrangler.toml): pings the origin so Render's free plan doesn't put it to sleep,
  // which would drop the in-memory Kite session.
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(fetch(new URL("/healthz", env.ORIGIN_URL)));
  },
};
