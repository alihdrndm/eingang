import { describe, expect, it } from "vitest";

import {
  changes,
  display,
  FIELDS,
  fieldRows,
  type FieldDef,
  type InvoiceDetail,
  isMono,
  parseAmount,
  parseDate,
  validate,
} from "./fields";

function invoice(overrides: Partial<InvoiceDetail> = {}): InvoiceDetail {
  return {
    is_einvoice: false,
    notes: [],
    tax_breakdown: [],
    field_confidence: {},
    field_evidence: {},
    extraction_method: "llm",
    ...overrides,
  };
}

function def(name: string): FieldDef {
  const found = FIELDS.find((candidate) => candidate.name === name);
  if (!found) throw new Error(name);
  return found;
}

describe("field rows", () => {
  it("carries the LLM's confidence and evidence", () => {
    const rows = fieldRows(
      invoice({
        invoice_number: "2026-1043",
        field_confidence: { invoice_number: "high", buyer_vat_id: "low" },
        field_evidence: { invoice_number: "Rechnungsnummer: 2026-1043" },
      }),
    );
    const number = rows.find((row) => row.def.name === "invoice_number");
    expect(number).toMatchObject({
      value: "2026-1043",
      source: "llm",
      confidence: "high",
      evidence: "Rechnungsnummer: 2026-1043",
    });
    expect(
      rows.find((row) => row.def.name === "buyer_vat_id")?.confidence,
    ).toBe("low");
    expect(rows.find((row) => row.def.name === "due_date")?.value).toBeNull();
  });

  it("shows no confidence for XML values unless a person edited them", () => {
    const rows = fieldRows(
      invoice({
        extraction_method: "xml",
        field_confidence: { invoice_number: "high", payee_iban: "edited" },
      }),
    );
    expect(
      rows.find((row) => row.def.name === "invoice_number")?.confidence,
    ).toBeNull();
    expect(rows.find((row) => row.def.name === "payee_iban")?.confidence).toBe(
      "edited",
    );
  });

  it("lists every editable field once, with its business term", () => {
    const names = FIELDS.map((candidate) => candidate.name);
    expect(new Set(names).size).toBe(names.length);
    expect(def("gross_total").term).toBe("BT-112");
    expect(def("seller_name").label).toBe("Supplier");
  });
});

describe("validation", () => {
  it.each([
    [
      "issue_date",
      "2026-02-30x",
      "Enter the date as DD.MM.YYYY or YYYY-MM-DD.",
    ],
    ["issue_date", "2026-02-30", "Enter the date as DD.MM.YYYY or YYYY-MM-DD."],
    ["issue_date", "30.02.2026", "Enter the date as DD.MM.YYYY or YYYY-MM-DD."],
    ["issue_date", "20.02.26", "Enter the date as DD.MM.YYYY or YYYY-MM-DD."],
    ["issue_date", "2026-02-20", null],
    ["issue_date", "20.02.2026", null],
    ["issue_date", "1.3.2026", null],
    ["gross_total", "1.547,00", null],
    ["gross_total", "1547,00", null],
    ["gross_total", "1 547,00", null],
    ["gross_total", "1547.00", null],
    ["gross_total", "1547", null],
    ["gross_total", "-58,31", null],
    ["gross_total", "1.547", "Enter an amount like 1.190,00 or 1190.00."],
    ["gross_total", "0,125", "Enter an amount like 1.190,00 or 1190.00."],
    ["gross_total", "12,34,56", "Enter an amount like 1.190,00 or 1190.00."],
    ["gross_total", "1.547,00 €", "Enter an amount like 1.190,00 or 1190.00."],
    ["gross_total", "abc", "Enter an amount like 1.190,00 or 1190.00."],
    ["seller_email", "nope", "Enter an email address."],
    ["seller_country_code", "deu", "Enter a two-letter country code."],
    ["seller_country_code", "de", null],
    ["type_code", "38", "Enter the three-digit type code, like 380."],
    ["invoice_number", "anything", null],
    ["due_date", "  ", null],
  ])("%s %j", (name, value, expected) => {
    expect(validate(def(name), value)).toBe(expected);
  });
});

describe("parsing typed values", () => {
  it.each([
    ["1.190,00", "1190.00"],
    ["1190,00", "1190.00"],
    ["1 190,00", "1190.00"],
    ["1 190,00", "1190.00"],
    ["1.190.000,50", "1190000.50"],
    ["1,190.00", "1190.00"],
    ["1190.00", "1190.00"],
    ["1190", "1190"],
    ["1190,5", "1190.5"],
    ["-58,31", "-58.31"],
    // One mark followed by exactly three digits: the backend's rule (docs/DECISIONS.md).
    ["1,190", "1190"],
    ["1.190", "1.190"],
    ["0,190", "0.190"],
    ["12,34,56", null],
    ["1.190,00 EUR", null],
    ["", null],
  ])("amount %j is %j", (typed, expected) => {
    expect(parseAmount(typed)).toBe(expected);
  });

  it.each([
    ["2026-10-23", "2026-10-23"],
    ["23.10.2026", "2026-10-23"],
    ["3.1.2026", "2026-01-03"],
    [" 03.01.2026 ", "2026-01-03"],
    ["29.02.2028", "2028-02-29"],
    ["29.02.2026", null],
    ["2026-13-01", null],
    ["10/23/2026", null],
  ])("date %j is %j", (typed, expected) => {
    expect(parseDate(typed)).toBe(expected);
  });
});

describe("changes", () => {
  it("sends amounts and dates in the API's format", () => {
    const rows = fieldRows(invoice({ gross_total: "1190.00" }));
    expect(
      changes(rows, { gross_total: "1.190,00", due_date: "1.4.2026" }),
    ).toEqual({ due_date: "2026-04-01" });
    expect(changes(rows, { gross_total: "1 547,10" })).toEqual({
      gross_total: "1547.10",
    });
  });

  it("sends only changed fields; empty text clears", () => {
    const rows = fieldRows(
      invoice({ invoice_number: "A-1", currency: "EUR", type_code: 380 }),
    );
    const body = changes(rows, {
      invoice_number: "A-1",
      currency: "",
      type_code: "381",
      seller_country_code: "de",
      due_date: "2026-04-01",
    });
    expect(body).toEqual({
      currency: null,
      type_code: 381,
      seller_country_code: "DE",
      due_date: "2026-04-01",
    });
  });
});

describe("display", () => {
  it("formats amounts and dates as designed", () => {
    expect(display(def("gross_total"), "1190.00")).toBe("1.190,00 €");
    expect(display(def("gross_total"), "-58.31")).toBe("-58,31 €");
    expect(display(def("gross_total"), "10.00", "USD")).toBe("10,00 USD");
    expect(display(def("due_date"), "2026-10-23")).toBe("23 Oct 2026");
    expect(display(def("gross_total"), "58.31", "EUR", true)).toBe("-58,31 €");
    expect(display(def("gross_total"), "0.00", "EUR", true)).toBe("0,00 €");
    expect(display(def("seller_name"), null)).toBe("—");
    expect(isMono(def("payee_iban"))).toBe(true);
    expect(isMono(def("seller_name"))).toBe(false);
  });
});
