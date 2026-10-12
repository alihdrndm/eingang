import { type QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia, type Pinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, type Router } from "vue-router";

import { api, resetCsrfToken } from "./api/client";
import { createQueryClient } from "./api/query";
import App from "./App.vue";
import { createAppRouter } from "./router";
import { SANDBOX_EXPIRED_MESSAGE } from "./stores/session";
import {
  json,
  mockFetch,
  problem,
  sessionBody,
  setCsrfCookie,
} from "./testing/http";

let pinia: Pinia;
let router: Router;
let mounted: { wrapper: VueWrapper; queryClient: QueryClient } | null = null;

beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
  router = createAppRouter(createMemoryHistory());
  resetCsrfToken();
  setCsrfCookie("token");
});

afterEach(() => {
  // Tear the app down so its queries (a retry waits a second) cannot fetch during the next test
  // and take the responses that test queued for itself.
  mounted?.wrapper.unmount();
  mounted?.queryClient.clear();
  mounted = null;
  vi.unstubAllGlobals();
});

async function visit(path: string) {
  await router.push(path);
  await router.isReady();
  const queryClient = createQueryClient();
  const wrapper = mount(App, {
    global: {
      plugins: [pinia, router, [VueQueryPlugin, { queryClient }]],
    },
  });
  mounted = { wrapper, queryClient };
  await flushPromises();
  return wrapper;
}

describe("public routes", () => {
  it.each([
    ["/", "Some of our invoices are e-invoices"],
    ["/sign-in", "Sign in"],
    ["/accuracy", "How accurately does Eingang read invoices?"],
  ])("%s renders without asking for a session", async (path, heading) => {
    const calls = mockFetch(() => problem(401, "NOT_AUTHENTICATED"));

    const wrapper = await visit(path);

    expect(router.currentRoute.value.path).toBe(path);
    expect(wrapper.get("h1").text()).toContain(heading);
    // Public pages never ask who is signed in (they may load public data).
    expect(
      calls.filter((call) => call.path.startsWith("/api/v1/auth")),
    ).toEqual([]);
  });

  it("shows the not-found screen for unknown paths", async () => {
    mockFetch(() => problem(401, "NOT_AUTHENTICATED"));
    const wrapper = await visit("/nowhere/at/all");
    expect(wrapper.get("h1").text()).toBe("Nothing at this address");
  });
});

describe("app routes", () => {
  it("let a signed-in user in and ask for the session only once", async () => {
    const calls = mockFetch(() => json(200, sessionBody()));

    const wrapper = await visit("/app/inbox");
    expect(router.currentRoute.value.name).toBe("inbox");
    expect(wrapper.get("h1").text()).toBe("Inbox");
    expect(wrapper.text()).not.toContain("Sandbox ·");

    await router.push("/app/settings");
    await flushPromises();
    expect(wrapper.get("h1").text()).toBe("Settings");
    expect(
      calls.filter((call) => call.path === "/api/v1/auth/me"),
    ).toHaveLength(1);
  });

  it("show the sandbox banner in a sandbox", async () => {
    const expiresAt = new Date(
      Date.now() + 22.5 * 60 * 60 * 1000,
    ).toISOString();
    mockFetch(() =>
      json(200, sessionBody({ kind: "sandbox", expires_at: expiresAt })),
    );

    const wrapper = await visit("/app/inbox");

    expect(wrapper.get(".sandbox-banner").text()).toBe(
      "Sandbox · deleted in 23 h · sample data, nothing is real",
    );
  });

  it("send visitors without a session to sign-in", async () => {
    mockFetch(() => problem(401, "NOT_AUTHENTICATED"));

    await visit("/app/approvals");

    expect(router.currentRoute.value.name).toBe("sign-in");
    expect(router.currentRoute.value.query.redirect).toBe("/app/approvals");
  });

  it("send an expired sandbox to the homepage with the message", async () => {
    mockFetch(() => problem(401, "SANDBOX_EXPIRED", SANDBOX_EXPIRED_MESSAGE));

    const wrapper = await visit("/app/inbox");

    expect(router.currentRoute.value.name).toBe("home");
    expect(wrapper.get('[role="status"]').text()).toBe(SANDBOX_EXPIRED_MESSAGE);
  });

  it("redirect /app to the inbox", async () => {
    mockFetch(() => json(200, sessionBody()));
    await visit("/app");
    expect(router.currentRoute.value.name).toBe("inbox");
  });
});

describe("401 from any request", () => {
  it("NOT_AUTHENTICATED on an app page goes to sign-in", async () => {
    const responses = [
      json(200, sessionBody()),
      problem(401, "NOT_AUTHENTICATED"),
    ];
    mockFetch(() => responses.shift() ?? json(200, {}));
    await visit("/app/exports");

    await api.GET("/api/v1/stats");
    await flushPromises();

    expect(router.currentRoute.value.name).toBe("sign-in");
    expect(router.currentRoute.value.query.redirect).toBe("/app/exports");
  });

  it("SANDBOX_EXPIRED on an app page goes home with the message", async () => {
    const responses = [
      json(
        200,
        sessionBody({ kind: "sandbox", expires_at: "2099-01-01T00:00:00Z" }),
      ),
      problem(401, "SANDBOX_EXPIRED", SANDBOX_EXPIRED_MESSAGE),
    ];
    mockFetch(() => responses.shift() ?? json(200, {}));
    const wrapper = await visit("/app/inbox");

    await api.GET("/api/v1/stats");
    await flushPromises();

    expect(router.currentRoute.value.name).toBe("home");
    expect(wrapper.get('[role="status"]').text()).toBe(SANDBOX_EXPIRED_MESSAGE);
  });

  it("does nothing on a public page", async () => {
    mockFetch(() => problem(401, "NOT_AUTHENTICATED"));
    await visit("/accuracy");

    await api.GET("/api/v1/stats");
    await flushPromises();

    expect(router.currentRoute.value.name).toBe("accuracy-public");
  });

  it("403 never signs out", async () => {
    const responses = [
      json(200, sessionBody()),
      problem(403, "FORBIDDEN_ROLE"),
    ];
    mockFetch(() => responses.shift() ?? json(200, {}));
    await visit("/app/settings");

    await api.GET("/api/v1/members");
    await flushPromises();

    expect(router.currentRoute.value.name).toBe("settings");
  });
});
