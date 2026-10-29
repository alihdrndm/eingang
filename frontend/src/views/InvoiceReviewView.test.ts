import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia, type Pinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import {
  createMemoryHistory,
  createRouter,
  type Router,
  RouterView,
} from "vue-router";

import { resetCsrfToken } from "../api/client";
import { queryKeys } from "../api/query";
import type { components } from "../api/schema";
import { useToast } from "../components/ui/useToast";
import FieldsPanel from "../features/review/FieldsPanel.vue";
import { DOC_ID, detail, summary } from "../features/review-screen/testing";
import { useSessionStore } from "../stores/session";
import {
  json,
  mockFetch,
  problem,
  type RecordedRequest,
  sessionBody,
  setCsrfCookie,
} from "../testing/http";
import InvoiceReviewView from "./InvoiceReviewView.vue";

type DocumentDetail = components["schemas"]["DocumentDetail"];

// PDF.js does not run in jsdom: a one-page stand-in.
vi.mock("../features/viewer/pdf", () => ({
  openPdf: vi.fn(async () => ({
    numPages: 1,
    loadingTask: { destroy: vi.fn(async () => undefined) },
    getPage: vi.fn(async () => ({
      getViewport: () => ({ width: 100, height: 140 }),
      render: () => ({ promise: Promise.resolve(), cancel: () => undefined }),
    })),
  })),
}));

let pinia: Pinia;
let router: Router;
let queryClient: QueryClient;
let wrapper: VueWrapper | null = null;
const Stub = { template: "<p>inbox</p>" };
const App = defineComponent({ render: () => h(RouterView) });

const path = `/api/v1/documents/${DOC_ID}`;

beforeEach(async () => {
  pinia = createPinia();
  setActivePinia(pinia);
  useSessionStore().me = { ...sessionBody(), role: "accountant" };
  resetCsrfToken();
  setCsrfCookie("token");
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: "/app/invoices/:id",
        name: "invoice",
        component: InvoiceReviewView,
        props: true,
      },
      { path: "/app/inbox", name: "inbox", component: Stub },
    ],
  });
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  useToast().dismiss();
  vi.unstubAllGlobals();
});

async function mountAt(url = `/app/invoices/${DOC_ID}`): Promise<VueWrapper> {
  await router.push(url);
  await router.isReady();
  wrapper = mount(App, {
    attachTo: document.body,
    global: {
      plugins: [pinia, router, [VueQueryPlugin, { queryClient }]],
    },
  });
  await flushPromises();
  return wrapper;
}

/** Answers the detail with `doc`, the file and text with stand-ins, everything else via `extra`. */
function serve(
  doc: DocumentDetail,
  extra: (request: RecordedRequest) => Response | undefined = () => undefined,
): RecordedRequest[] {
  return mockFetch((request) => {
    const answer = extra(request);
    if (answer) return answer;
    if (
      request.method === "GET" &&
      request.path === `/api/v1/documents/${doc.id}`
    )
      return json(200, doc);
    if (request.path.endsWith("/file"))
      return new Response(new Uint8Array([37, 80, 68, 70]), { status: 200 });
    if (request.path.endsWith("/text"))
      return new Response("Rechnung 2026-1043\nGesamtbetrag 1.547,00 €\n", {
        status: 200,
        headers: { "Content-Type": "text/plain" },
      });
    if (request.path.endsWith("/visualization"))
      return new Response("<html></html>", {
        status: 200,
        headers: { "Content-Type": "text/html" },
      });
    return problem(
      404,
      "NOT_AVAILABLE",
      "This document has no such representation.",
    );
  });
}

function button(view: VueWrapper, label: string) {
  const found = view
    .findAll("button")
    .find((item) => item.text().trim() === label);
  if (!found) throw new Error(`No button "${label}"`);
  return found;
}

describe("InvoiceReviewView data states", () => {
  it("shows a skeleton while the invoice loads", async () => {
    mockFetch(() => new Promise<Response>(() => undefined));
    const view = await mountAt();
    expect(
      view
        .find('[aria-busy="true"][aria-label="Loading the invoice"]')
        .exists(),
    ).toBe(true);
  });

  it("shows the problem's title and detail and tries again", async () => {
    let calls = 0;
    mockFetch(() => {
      calls += 1;
      return problem(500, "INTERNAL", "The server could not answer.");
    });
    const view = await mountAt();
    expect(view.text()).toContain("INTERNAL");
    expect(view.text()).toContain("The server could not answer.");
    await button(view, "Try again").trigger("click");
    await flushPromises();
    expect(calls).toBe(2);
  });

  it("shows the not-found page for an unknown invoice", async () => {
    mockFetch(() => problem(404, "NOT_FOUND", "Not found."));
    const view = await mountAt();
    expect(view.find("h1").text()).toBe("Nothing at this address");
    expect(view.find('a[href="/app/inbox"]').text()).toBe("Go to the Inbox");
  });

  it("shows the invoice: supplier, amount, status, validation and the truncation note", async () => {
    serve(
      detail({
        type_code: 381,
        gross_total: "58.31",
        text_truncated: true,
        validation: {
          status: "invalid",
          engine: "xrechnung-config 2026-01-31; CEN 1; saxonche 1",
          fatal_count: 1,
          warning_count: 0,
          issues: [
            {
              rule_id: "BR-DE-15",
              severity: "fatal",
              message:
                "[BR-DE-15] Das Element „Buyer reference“ (BT-10) muss übermittelt werden.",
              location: "/Invoice",
              test: "t",
              source: "xrechnung",
              explanation: {
                plain_text:
                  "XRechnung invoices must name the buyer's reference.",
                fix_hint:
                  "Ask the supplier to resend the invoice with your reference.",
              },
            },
          ],
        },
      }),
    );
    const view = await mountAt();
    expect(view.find("h1").text()).toBe("Druckerei Sommer GmbH");
    expect(view.text()).toContain("−58,31 €");
    expect(view.text()).toContain("Needs review");
    expect(view.text()).toContain("Invalid · 1 error");
    expect(view.text()).toContain("Only the first 12,000 characters were read");
    expect(view.text()).toContain("BR-DE-15");
    expect(view.text()).toContain(
      "XRechnung invoices must name the buyer's reference.",
    );

    // The official message is behind "Show official message".
    expect(view.text()).not.toContain("muss übermittelt werden");
    const toggle = button(view, "Show official message");
    expect(toggle.attributes("aria-expanded")).toBe("false");
    await toggle.trigger("click");
    expect(view.find('blockquote[lang="de"]').text()).toContain(
      "muss übermittelt werden",
    );
  });

  it("polls while the invoice is still being processed", async () => {
    vi.useFakeTimers();
    try {
      const calls = serve(
        detail({ status: "processing", processing_step: "validate" }),
      );
      await mountAt();
      const detailCalls = () =>
        calls.filter((call) => call.method === "GET" && call.path === path)
          .length;
      expect(detailCalls()).toBe(1);
      await vi.advanceTimersByTimeAsync(2100);
      expect(detailCalls()).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("InvoiceReviewView actions", () => {
  it("shows why a disabled action is unavailable", async () => {
    serve(
      detail({
        allowed_actions: [
          {
            action: "mark_reviewed",
            enabled: false,
            reason_code: "BLOCKING_CHECKS",
            reason: "Resolve 1 blocking check first.",
          },
          {
            action: "delete",
            enabled: false,
            reason_code: "FORBIDDEN_ROLE",
            reason: "Your role (accountant) can't do this.",
          },
        ],
      }),
    );
    const view = await mountAt();
    const markReviewed = button(view, "Mark reviewed");
    expect(markReviewed.attributes("disabled")).toBeDefined();
    const describedBy = markReviewed.attributes("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy ?? "")?.textContent).toBe(
      "Resolve 1 blocking check first.",
    );
    // Also visible as text, not only greyed out.
    const reasons = view.findAll(".reason").map((item) => item.text());
    expect(reasons).toEqual([
      "Resolve 1 blocking check first.",
      "Your role (accountant) can't do this.",
    ]);
  });

  it("needs a reason to reject, then posts it", async () => {
    const doc = detail({
      status: "awaiting_approval",
      allowed_actions: [
        { action: "approve", enabled: true },
        { action: "reject", enabled: true },
      ],
    });
    const rejected = detail({ status: "rejected", allowed_actions: [] });
    let decided = false;
    const calls = serve(doc, (request) => {
      if (request.method === "POST") {
        decided = true;
        return json(200, rejected);
      }
      if (decided && request.path === path) return json(200, rejected);
      return undefined;
    });
    const view = await mountAt();
    await button(view, "Reject…").trigger("click");
    await flushPromises();
    expect(document.body.textContent).toContain("Reject invoice 2026-1043?");

    await button(view, "Reject invoice").trigger("click");
    await flushPromises();
    expect(view.text()).toContain("Write at least 5 characters.");
    expect(calls.some((call) => call.method === "POST")).toBe(false);

    await view.find("textarea").setValue("Wrong VAT ID on the invoice.");
    await button(view, "Reject invoice").trigger("click");
    await flushPromises();
    const post = calls.find((call) => call.method === "POST");
    expect(post?.path).toBe(`${path}/decision`);
    expect(JSON.parse(post?.body ?? "{}")).toEqual({
      decision: "rejected",
      comment: "Wrong VAT ID on the invoice.",
    });
    expect(useToast().toast.value?.message).toBe("You rejected 2026-1043.");
    expect(view.text()).toContain("Rejected");
  });

  it("shows how many blocking checks stop Mark reviewed", async () => {
    serve(
      detail({ allowed_actions: [{ action: "mark_reviewed", enabled: true }] }),
      (request) =>
        request.method === "POST"
          ? problem(
              409,
              "BLOCKING_CHECKS",
              "Resolve 2 blocking checks first.",
              {
                title: "Blocking checks remain",
                checks: ["c1", "c2"],
              },
            )
          : undefined,
    );
    const view = await mountAt();
    await button(view, "Mark reviewed").trigger("click");
    await flushPromises();
    expect(useToast().toast.value).toMatchObject({
      kind: "error",
      message: "Blocking checks remain. Resolve 2 blocking checks first.",
    });
  });
});

describe("InvoiceReviewView document viewer", () => {
  function tab(view: VueWrapper, label: string) {
    const found = view
      .findAll('.viewer [role="tab"]')
      .find((item) => item.text().startsWith(label));
    if (!found) throw new Error(`No tab "${label}"`);
    return found;
  }

  it("disables the XML tab for a PDF without XML, with the reason", async () => {
    serve(detail({ kind: "pdf_text" }));
    const view = await mountAt();
    const xml = tab(view, "XML");
    expect(xml.attributes("aria-disabled")).toBe("true");
    expect(xml.text()).toContain("This PDF has no XML inside");
    expect(tab(view, "Text").attributes("aria-disabled")).toBeUndefined();
    expect(view.find("canvas").exists()).toBe(true);
  });

  it("disables the Text tab for XML invoices and shows the rendered view", async () => {
    serve(
      detail({
        kind: "xml",
        format_label: "XRechnung · UBL",
        original_filename: "re.xml",
      }),
    );
    const view = await mountAt();
    const text = tab(view, "Text");
    expect(text.attributes("aria-disabled")).toBe("true");
    expect(text.text()).toContain(
      "This invoice is XML only, there is no PDF text",
    );
    const frame = view.find("iframe");
    expect(frame.attributes("sandbox")).toBe("");
    expect(frame.attributes("src")).toBe(`${path}/visualization`);
    expect(frame.attributes("title")).toBe(
      "Rendered view of invoice 2026-1043",
    );
  });

  it("falls back to the XML when there is no rendered view", async () => {
    serve(
      detail({
        kind: "xml",
        format_label: "XRechnung · UBL",
        original_filename: "re.xml",
      }),
      (request) =>
        request.path.endsWith("/xml")
          ? new Response("<Invoice><cbc:ID>RE-1</cbc:ID></Invoice>", {
              status: 200,
              headers: { "Content-Type": "application/xml" },
            })
          : request.path.endsWith("/visualization")
            ? problem(404, "NOT_AVAILABLE")
            : undefined,
    );
    const view = await mountAt();
    await flushPromises();
    expect(tab(view, "XML").attributes("aria-selected")).toBe("true");
    expect(tab(view, "Document").attributes("aria-disabled")).toBe("true");
    expect(view.find(".tok--name").text()).toBe("Invoice");
    expect(view.find(".code-number").text()).toBe("1");
  });

  it("marks the selected field's evidence in the Text tab", async () => {
    serve(
      detail({
        invoice: {
          is_einvoice: false,
          notes: [],
          tax_breakdown: [],
          field_confidence: { gross_total: "high" },
          field_evidence: { gross_total: "Gesamtbetrag 1.547,00 €" },
          extraction_method: "llm",
          gross_total: "1547.00",
        },
      }),
    );
    const view = await mountAt();
    view.findComponent(FieldsPanel).vm.$emit("select-field", "gross_total");
    await flushPromises();
    expect(tab(view, "Text").attributes("aria-selected")).toBe("true");
    const mark = view.find("mark.evidence");
    expect(mark.text()).toBe("Gesamtbetrag 1.547,00 €");
    expect(
      mark.element.closest(".text-line")?.classList.contains("is-hit"),
    ).toBe(true);
  });
});

describe("InvoiceReviewView timeline", () => {
  it("writes every event in plain words, newest first, without IBAN values", async () => {
    serve(
      detail({
        status: "awaiting_approval",
        validation: {
          status: "valid",
          engine: "xrechnung-config 2026-01-31",
          fatal_count: 0,
          warning_count: 0,
          issues: [],
        },
        events: [
          {
            type: "review.completed",
            actor_name: "Anna Weber",
            data: { from: "needs_review", to: "awaiting_approval" },
            created_at: "2026-10-08T14:05:00Z",
          },
          {
            type: "check.cleared",
            data: { check_id: "C05", code: "BANK_DETAILS_CHANGED" },
            created_at: "2026-10-08T14:00:01Z",
          },
          {
            type: "invoice.fields_edited",
            actor_name: "Anna Weber",
            data: {
              field: "payee_iban",
              change: "changed",
              old: "DE…3000",
              new: "DE…2051",
            },
            created_at: "2026-10-08T14:00:00Z",
          },
          {
            type: "processing.step",
            data: { note: "Visible PDF not compared" },
            created_at: "2026-10-07T12:30:02Z",
          },
          {
            type: "processing.completed",
            data: { from: "processing", to: "needs_review" },
            created_at: "2026-10-07T12:30:01Z",
          },
          {
            type: "document.received",
            data: { source: "upload", size_bytes: 10 },
            created_at: "2026-10-07T12:30:00Z",
          },
        ],
      }),
    );
    const view = await mountAt();
    const titles = view.findAll(".entry-title").map((item) => item.text());
    expect(titles).toEqual([
      "Waiting for approval",
      "Marked reviewed by Anna Weber",
      "Check no longer applies",
      "IBAN edited by Anna Weber",
      "Visible PDF not compared",
      "Checked: valid e-invoice",
      "Received",
    ]);
    expect(view.find(".activity").text()).not.toMatch(/DE\d{2}/);
    expect(view.find(".activity").text()).not.toContain("3000");
    expect(view.find(".activity").text()).toContain(
      "C05 · bank details changed",
    );
  });
});

describe("InvoiceReviewView j / k", () => {
  function press(key: string, target: EventTarget = window): void {
    target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  }

  it("opens the next and previous invoice of the inbox list, wrapping round", async () => {
    const list = [summary(DOC_ID), summary("d2"), summary("d3")];
    queryClient.setQueryData(
      queryKeys.documents.list({ status: "needs_review" }),
      {
        count: 3,
        next: null,
        previous: null,
        results: list,
      },
    );
    mockFetch((request) => {
      const id = request.path.split("/")[4] ?? "";
      if (
        request.method === "GET" &&
        request.path === `/api/v1/documents/${id}`
      )
        return json(200, detail({ id, supplier_name: `Supplier ${id}` }));
      return problem(404, "NOT_AVAILABLE");
    });
    await mountAt(`/app/invoices/${DOC_ID}?from=status%3Dneeds_review`);

    press("j");
    await flushPromises();
    expect(router.currentRoute.value.params.id).toBe("d2");
    expect(router.currentRoute.value.query.from).toBe("status=needs_review");
    expect(useToast().toast.value?.message).toBe("Next: Supplier d2, NR-d2");

    press("k");
    await flushPromises();
    press("k");
    await flushPromises();
    expect(router.currentRoute.value.params.id).toBe("d3");
    expect(useToast().toast.value?.message).toBe(
      "Back to the end of the list. Previous: Supplier d3, NR-d3",
    );
  });

  it("fetches the list when none is cached and ignores keys while typing", async () => {
    const calls = mockFetch((request) => {
      if (request.path === "/api/v1/documents")
        return json(200, {
          count: 2,
          next: null,
          previous: null,
          results: [summary(DOC_ID), summary("d2")],
        });
      if (request.path === path) return json(200, detail());
      if (request.path === "/api/v1/documents/d2")
        return json(200, detail({ id: "d2" }));
      return problem(404, "NOT_AVAILABLE");
    });
    const view = await mountAt();

    const input = document.createElement("input");
    document.body.append(input);
    press("j", input);
    await flushPromises();
    expect(router.currentRoute.value.params.id).toBe(DOC_ID);
    input.remove();

    press("j");
    await flushPromises();
    expect(calls.some((call) => call.path === "/api/v1/documents")).toBe(true);
    expect(router.currentRoute.value.params.id).toBe("d2");
    expect(view.exists()).toBe(true);
  });
});
