import { nextTick } from "vue";
import {
  createRouter,
  createWebHistory,
  type RouteLocationRaw,
  type Router,
  type RouterHistory,
  type RouteRecordRaw,
  START_LOCATION,
} from "vue-router";

import { setAuthFailureHandler } from "./api/client";
import { useSessionStore } from "./stores/session";

declare module "vue-router" {
  interface RouteMeta {
    /** The route needs a signed-in session (every `/app/*` route). */
    requiresAuth?: boolean;
    /** The document title ("Inbox · Eingang"), WCAG 2.4.2. Pages may refine it once loaded. */
    title?: string;
  }
}

const APP_NAME = "Eingang";

export const routes: RouteRecordRaw[] = [
  {
    path: "/",
    name: "home",
    component: () => import("./views/HomeView.vue"),
    meta: { title: APP_NAME },
  },
  {
    path: "/accuracy",
    name: "accuracy-public",
    component: () => import("./views/AccuracyPublicView.vue"),
    meta: { title: "Accuracy · Eingang" },
  },
  {
    path: "/sign-in",
    name: "sign-in",
    component: () => import("./views/SignInView.vue"),
    meta: { title: "Sign in · Eingang" },
  },
  {
    path: "/app",
    component: () => import("./views/AppFrame.vue"),
    meta: { requiresAuth: true },
    children: [
      { path: "", redirect: { name: "inbox" } },
      {
        path: "inbox",
        name: "inbox",
        component: () => import("./views/InboxView.vue"),
        meta: { title: "Inbox · Eingang" },
      },
      {
        path: "invoices/:id",
        name: "invoice",
        component: () => import("./views/InvoiceReviewView.vue"),
        meta: { title: "Invoice review · Eingang" },
        props: true,
      },
      {
        path: "approvals",
        name: "approvals",
        component: () => import("./views/ApprovalsView.vue"),
        meta: { title: "Approvals · Eingang" },
      },
      {
        path: "suppliers",
        name: "suppliers",
        component: () => import("./views/SuppliersView.vue"),
        meta: { title: "Suppliers · Eingang" },
      },
      {
        path: "suppliers/:id",
        name: "supplier",
        component: () => import("./views/SupplierDetailView.vue"),
        meta: { title: "Supplier · Eingang" },
        props: true,
      },
      {
        path: "exports",
        name: "exports",
        component: () => import("./views/ExportsView.vue"),
        meta: { title: "Exports · Eingang" },
      },
      {
        path: "accuracy",
        name: "accuracy",
        component: () => import("./views/AccuracyView.vue"),
        meta: { title: "Accuracy · Eingang" },
      },
      {
        path: "settings",
        name: "settings",
        component: () => import("./views/SettingsView.vue"),
        meta: { title: "Settings · Eingang" },
      },
    ],
  },
  {
    path: "/:pathMatch(.*)*",
    name: "not-found",
    component: () => import("./views/NotFoundView.vue"),
    meta: { title: "Not found · Eingang" },
  },
];

/** Where a signed-out visitor of `fullPath` is sent. */
function signInLocation(fullPath: string): RouteLocationRaw {
  return { name: "sign-in", query: { redirect: fullPath } };
}

/** Sends `/app/*` visitors without a session to sign-in, and expired sandboxes to the homepage. */
export function installGuards(router: Router): void {
  router.beforeEach(async (to) => {
    if (!to.matched.some((record) => record.meta.requiresAuth)) return true;
    const session = useSessionStore();
    try {
      if (await session.load()) return true;
    } catch {
      // The API could not answer (network or 5xx): the app cannot run without a session.
      return signInLocation(to.fullPath);
    }
    return session.notice ? { name: "home" } : signInLocation(to.fullPath);
  });

  setAuthFailureHandler((code) => {
    const session = useSessionStore();
    session.handleAuthFailure(code);
    const current = router.currentRoute.value;
    if (!current.path.startsWith("/app")) return;
    void router.push(
      code === "SANDBOX_EXPIRED"
        ? { name: "home" }
        : signInLocation(current.fullPath),
    );
  });
}

/** How long focus follows a page's replaced <h1> (a slow invoice load). */
const FOLLOW_MS = 10_000;

let stopFollowing: (() => void) | null = null;

function isTextField(element: Element | null): boolean {
  return (
    element instanceof HTMLInputElement ||
    element instanceof HTMLTextAreaElement ||
    element instanceof HTMLSelectElement ||
    (element instanceof HTMLElement && element.isContentEditable)
  );
}

/**
 * Moves keyboard focus to the new page's <h1> (WCAG 2.4.3), which also makes screen readers
 * announce the page. Pages swap their loading <h1> for the loaded one; while focus is still
 * where it was put, it follows to the replacement, until the user moves it or navigates on.
 */
function focusPageHeading(): void {
  stopFollowing?.();
  // Someone is typing in a field that outlives the page (the top-bar search, which opens the
  // inbox as you type): leave them there.
  if (isTextField(document.activeElement)) return;
  const focus = (): HTMLElement | null => {
    const heading = document.querySelector<HTMLElement>("h1");
    if (!heading) return null;
    if (!heading.hasAttribute("tabindex"))
      heading.setAttribute("tabindex", "-1");
    heading.focus();
    return heading;
  };
  let focused = focus();
  const observer = new MutationObserver(() => {
    const active = document.activeElement;
    if (focused?.isConnected || (active && active !== document.body)) return;
    focused = focus();
  });
  const onFocusIn = (event: FocusEvent): void => {
    if (event.target !== focused) stop();
  };
  const stop = (): void => {
    observer.disconnect();
    document.removeEventListener("focusin", onFocusIn);
    clearTimeout(timer);
    stopFollowing = null;
  };
  observer.observe(document.body, { childList: true, subtree: true });
  document.addEventListener("focusin", onFocusIn);
  const timer = setTimeout(stop, FOLLOW_MS);
  stopFollowing = stop;
}

/** Sets the document title on every navigation and moves focus when the page changes. */
export function installPageChange(router: Router): void {
  router.afterEach((to, from, failure) => {
    if (failure) return;
    document.title = to.meta.title ?? APP_NAME;
    // Not on the first load (the browser starts at the top of the page), and not when only the
    // query or hash changes (inbox tabs and filters keep focus where it is).
    if (from === START_LOCATION || to.path === from.path) return;
    void nextTick(focusPageHeading);
  });
}

export function createAppRouter(
  history: RouterHistory = createWebHistory(),
): Router {
  const router = createRouter({ history, routes });
  installGuards(router);
  installPageChange(router);
  return router;
}
