/**
 * Cloudflare Worker: puts your own domain in front of the Modal endpoint.
 *
 * Why a Worker and not just DNS: Modal routes by Host header and serves a *.modal.run
 * certificate, so a CNAME to the modal.run host breaks both routing and TLS. The Worker
 * terminates TLS on your domain, rewrites the request to the Modal origin, and keeps the
 * Modal API key out of clients (they use CLIENT_API_KEY instead).
 *
 * Secrets (wrangler secret put ...): ORIGIN_URL, ORIGIN_API_KEY, CLIENT_API_KEY
 */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/health" && request.method === "GET") {
      return fetch(`${env.ORIGIN_URL}/health`);
    }
    if (url.pathname !== "/v1/segment" || request.method !== "POST") {
      return new Response("not found", { status: 404 });
    }
    if (request.headers.get("x-api-key") !== env.CLIENT_API_KEY) {
      return new Response(JSON.stringify({ detail: "invalid api key" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      });
    }

    const headers = new Headers();
    headers.set("content-type", request.headers.get("content-type") ?? "");
    headers.set("x-api-key", env.ORIGIN_API_KEY);

    const origin = await fetch(`${env.ORIGIN_URL}/v1/segment`, {
      method: "POST",
      headers,
      body: request.body,
      redirect: "follow", // Modal answers 303 while a new revision builds its snapshot
    });

    const out = new Headers(origin.headers);
    out.set("cache-control", "no-store");
    return new Response(origin.body, { status: origin.status, headers: out });
  },
};
