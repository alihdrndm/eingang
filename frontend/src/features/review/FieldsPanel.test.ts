import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetCsrfToken } from "../../api/client";
import type { components } from "../../api/schema";
import { json, mockFetch, problem, setCsrfCookie } from "../../testing/http";
import FieldsPanel from "./FieldsPanel.vue";

type DocumentDetail = components["schemas"]["DocumentDetail"];

function documentWith(
  invoice: Record<string, unknown>,
  events: unknown[] = [],
): DocumentDetail {
  return {
    id: "doc-8",
    invoice: {
      is_einvoice: false,
      notes: [],
      tax_breakdown: [],
      field_confidence: {},
      field_evidence: {},
      extraction_method: "llm",
      currency: "EUR",
      ...invoice,
    },
    events,
  } as unknown as DocumentDetail;
}

const LLM = documentWith({
  invoice_number: "2026-1043",
  gross_total: "1547.00",
  buyer_vat_id: "DE29174",
  field_confidence: {
    invoice_number: "high",
    gross_total: "medium",
    buyer_vat_id: "low",
  },
  field_evidence: {
    invoice_number: "Rechnungsnummer: 2026-1043",
    buyer_vat_id: "USt-IdNr.: DE29174",
  },
});

beforeEach(() => setCsrfCookie("token"));
afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfCookie(null);
  resetCsrfToken();
  document.body.innerHTML = "";
});

function row(wrapper: ReturnType<typeof mount>, label: string) {
  const found = wrapper
    .findAll(".field")
    .find((candidate) => candidate.find("dt").text().includes(label));
  if (!found) throw new Error(`no row ${label}`);
  return found;
}

describe("FieldsPanel", () => {
  it("shows the LLM's confidence per field and tints low ones", () => {
    const wrapper = mount(FieldsPanel, {
      props: { document: LLM, canEdit: false },
    });
    expect(wrapper.text()).toContain("Read by AI from the PDF text.");
    expect(row(wrapper, "Invoice number").text()).toContain("High");
    expect(row(wrapper, "Gross").text()).toContain("1.547,00 €");
    expect(row(wrapper, "Gross").text()).toContain("Medium");
    expect(row(wrapper, "Buyer VAT ID").classes()).toContain("field--low");
    expect(wrapper.find('[aria-label="Edit Gross"]').exists()).toBe(false);
  });

  it("opens the evidence with the value marked and selects the field", async () => {
    const wrapper = mount(FieldsPanel, {
      props: { document: LLM, canEdit: false },
    });
    await wrapper
      .find('[aria-label="Show where Invoice number came from"]')
      .trigger("click");
    expect(wrapper.find("mark").text()).toBe("2026-1043");
    expect(wrapper.text()).toContain("Matches the document.");
    expect(wrapper.emitted("select-field")).toEqual([["invoice_number"]]);
  });

  it("marks XML values without confidence chips", () => {
    const xml = documentWith({
      extraction_method: "xml",
      invoice_number: "RE-2026-0412",
      field_confidence: { invoice_number: "high" },
    });
    const wrapper = mount(FieldsPanel, {
      props: { document: xml, canEdit: false },
    });
    expect(wrapper.text()).toContain(
      "From the XML. Read-only: the XML is the invoice.",
    );
    expect(row(wrapper, "Invoice number").find(".chip").exists()).toBe(false);
  });

  it("shows who edited a field", () => {
    const edited = documentWith(
      { invoice_number: "X-1", field_confidence: { invoice_number: "edited" } },
      [
        {
          type: "invoice.fields_edited",
          actor_name: "Anna Weber",
          data: { field: "invoice_number" },
        },
      ],
    );
    const wrapper = mount(FieldsPanel, {
      props: { document: edited, canEdit: false },
    });
    expect(row(wrapper, "Invoice number").text()).toContain(
      "Edited by Anna Weber",
    );
  });

  it("validates, then saves only the changed field", async () => {
    const calls = mockFetch(() => json(200, {}));
    const wrapper = mount(FieldsPanel, {
      props: { document: LLM, canEdit: true },
      attachTo: document.body,
    });
    await wrapper.find('[aria-label="Edit Gross"]').trigger("click");
    const input = wrapper.find("input");
    await input.setValue("1.547");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.text()).toContain(
      "Enter an amount like 1.190,00 or 1190.00.",
    );
    expect(calls).toHaveLength(0);
    await input.setValue("1.547,10");
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    const patch = calls.find((call) => call.method === "PATCH");
    expect(patch?.path).toBe("/api/v1/documents/doc-8/invoice");
    expect(JSON.parse(patch?.body ?? "{}")).toEqual({ gross_total: "1547.10" });
    expect(wrapper.emitted("saved")).toHaveLength(1);
    expect(document.activeElement).toBe(
      wrapper.find('[aria-label="Edit Gross"]').element,
    );
    wrapper.unmount();
  });

  it("asks for a decimal keyboard for amounts only", async () => {
    const wrapper = mount(FieldsPanel, {
      props: { document: LLM, canEdit: true },
      attachTo: document.body,
    });
    await wrapper.find('[aria-label="Edit Gross"]').trigger("click");
    expect(wrapper.find("input").attributes("inputmode")).toBe("decimal");
    await wrapper.find("input").trigger("keydown", { key: "Escape" });
    await wrapper.find('[aria-label="Edit Invoice number"]').trigger("click");
    expect(wrapper.find("input").attributes("inputmode")).toBeUndefined();
    wrapper.unmount();
  });

  it.each([
    [
      "Escape",
      (form: ReturnType<typeof mount>) =>
        form.find("input").trigger("keydown", { key: "Escape" }),
    ],
    [
      "Cancel",
      (form: ReturnType<typeof mount>) =>
        form
          .findAll("button")
          .find((button) => button.text() === "Cancel")
          ?.trigger("click"),
    ],
    [
      "Save without changes",
      (form: ReturnType<typeof mount>) => form.find("form").trigger("submit"),
    ],
  ])("returns focus to the Edit button after %s", async (_, end) => {
    const wrapper = mount(FieldsPanel, {
      props: { document: LLM, canEdit: true },
      attachTo: document.body,
    });
    await wrapper.find('[aria-label="Edit Gross"]').trigger("click");
    expect(document.activeElement).toBe(wrapper.find("input").element);

    await end(wrapper);
    await flushPromises();

    expect(wrapper.find("form").exists()).toBe(false);
    expect(document.activeElement).toBe(
      wrapper.find('[aria-label="Edit Gross"]').element,
    );
    wrapper.unmount();
  });

  it("shows the server's field error", async () => {
    mockFetch(() =>
      problem(422, "VALIDATION_FAILED", "One or more fields are invalid.", {
        errors: [
          { path: "buyer_vat_id", code: "max_length", message: "Too long." },
        ],
      }),
    );
    const wrapper = mount(FieldsPanel, {
      props: { document: LLM, canEdit: true },
      attachTo: document.body,
    });
    await wrapper.find('[aria-label="Edit Buyer VAT ID"]').trigger("click");
    await wrapper.find("input").setValue("DE291746055");
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(wrapper.find('[role="alert"]').text()).toBe("Too long.");
    expect(wrapper.emitted("saved")).toBeUndefined();
    wrapper.unmount();
  });

  it("cancels an edit with Escape", async () => {
    const wrapper = mount(FieldsPanel, {
      props: { document: LLM, canEdit: true },
      attachTo: document.body,
    });
    await wrapper.find('[aria-label="Edit Gross"]').trigger("click");
    await wrapper.find("input").trigger("keydown", { key: "Escape" });
    expect(wrapper.find("form").exists()).toBe(false);
    wrapper.unmount();
  });

  it("shows the evidence while the eye button has focus", async () => {
    const wrapper = mount(FieldsPanel, {
      props: { document: LLM, canEdit: false },
    });
    const eye = wrapper.find(
      '[aria-label="Show where Invoice number came from"]',
    );
    await eye.trigger("focus");
    expect(wrapper.find("mark").text()).toBe("2026-1043");
    await eye.trigger("blur");
    expect(wrapper.find("mark").exists()).toBe(false);
  });
});
