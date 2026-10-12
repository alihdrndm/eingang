// Vercel Routing Middleware for /api, run before the rewrite to the API in vercel.json.
// The rewrite reaches the API from a different Vercel address on every request, so the
// API's per-IP rate limits cannot find the visitor in X-Forwarded-For. This sends the
// visitor's address with a secret the API checks (backend/src/eingang/throttles.py).
import { ipAddress, next } from "@vercel/functions";

export const config = { matcher: "/api/:path*" };

export default function middleware(request: Request): Response {
  const headers = new Headers(request.headers);
  // Whatever the visitor sent under these names is never passed on.
  headers.delete("x-eingang-client-ip");
  headers.delete("x-eingang-proxy-secret");
  const secret = process.env.PROXY_SHARED_SECRET;
  const ip = ipAddress(request);
  if (secret && ip) {
    headers.set("x-eingang-client-ip", ip);
    headers.set("x-eingang-proxy-secret", secret);
  }
  return next({ request: { headers } });
}
