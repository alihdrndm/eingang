// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import middleware from "./middleware";

// next() hands the changed request headers to Vercel as x-middleware-request-* headers.
function forwarded(response: Response, name: string): string | null {
  return response.headers.get(`x-middleware-request-${name}`);
}

function visit(headers: Record<string, string>): Response {
  return middleware(
    new Request("https://eingang.vercel.app/api/v1/accuracy", { headers }),
  );
}

describe("API middleware", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("forwards the visitor's address with the secret", () => {
    vi.stubEnv("PROXY_SHARED_SECRET", "s3cret");
    const response = visit({ "x-real-ip": "203.0.113.9" });
    expect(forwarded(response, "x-eingang-client-ip")).toBe("203.0.113.9");
    expect(forwarded(response, "x-eingang-proxy-secret")).toBe("s3cret");
  });

  it("replaces an address and secret the visitor sent", () => {
    vi.stubEnv("PROXY_SHARED_SECRET", "s3cret");
    const response = visit({
      "x-real-ip": "203.0.113.9",
      "x-eingang-client-ip": "198.51.100.1",
      "x-eingang-proxy-secret": "guess",
    });
    expect(forwarded(response, "x-eingang-client-ip")).toBe("203.0.113.9");
    expect(forwarded(response, "x-eingang-proxy-secret")).toBe("s3cret");
  });

  it("forwards neither header without a secret", () => {
    vi.stubEnv("PROXY_SHARED_SECRET", "");
    const response = visit({
      "x-real-ip": "203.0.113.9",
      "x-eingang-client-ip": "198.51.100.1",
    });
    expect(forwarded(response, "x-eingang-client-ip")).toBeNull();
    expect(forwarded(response, "x-eingang-proxy-secret")).toBeNull();
  });
});
