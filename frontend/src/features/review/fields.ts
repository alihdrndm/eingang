// The invoice fields of the review screen (HANDOFF "Invoice review", design "FieldRow"):
// every editable canonical field with its EN 16931 business term, how it is shown, how it is
// checked before saving, and where its value came from (XML, read by the LLM, or a person).
import type { components } from "../../api/schema";

export type InvoiceDetail = components["schemas"]["InvoiceDetail"];
export type FieldKind =
  "text" | "date" | "amount" | "code" | "country" | "email" | "typeCode";
export type Confidence = "high" | "medium" | "low" | "edited";
export type FieldSource = "xml" | "llm" | "manual";
export type FieldName = (typeof FIELDS)[number]["name"];

export interface FieldDef {
  name: string;
  term: string; // EN 16931 business term, shown in mono
  label: string;
  kind: FieldKind;
  group: "invoice" | "supplier" | "buyer" | "payment" | "totals";
}

const field = (
  name: string,
  term: string,
  label: string,
  kind: FieldKind,
  group: FieldDef["group"],
): FieldDef => ({ name, term, label, kind, group });

export const FIELDS = [
  field("invoice_number", "BT-1", "Invoice number", "code", "invoice"),
  field("issue_date", "BT-2", "Issue date", "date", "invoice"),
  field("type_code", "BT-3", "Type", "typeCode", "invoice"),
  field("currency", "BT-5", "Currency", "code", "invoice"),
  field("due_date", "BT-9", "Due date", "date", "invoice"),
  field("buyer_reference", "BT-10", "Buyer reference", "text", "invoice"),
  field("order_reference", "BT-13", "Order reference", "text", "invoice"),
  field("seller_name", "BT-27", "Supplier", "text", "supplier"),
  field("seller_vat_id", "BT-31", "Supplier VAT ID", "code", "supplier"),
  field(
    "seller_tax_number",
    "BT-32",
    "Supplier tax number",
    "code",
    "supplier",
  ),
  field("seller_street", "BT-35", "Street", "text", "supplier"),
  field("seller_city", "BT-37", "City", "text", "supplier"),
  field("seller_postcode", "BT-38", "Postcode", "code", "supplier"),
  field("seller_country_code", "BT-40", "Country", "country", "supplier"),
  field("seller_email", "BT-43", "Email", "email", "supplier"),
  field("buyer_name", "BT-44", "Buyer", "text", "buyer"),
  field("buyer_vat_id", "BT-48", "Buyer VAT ID", "code", "buyer"),
  field("buyer_tax_number", "—", "Buyer tax number", "code", "buyer"),
  field("buyer_street", "BT-50", "Street", "text", "buyer"),
  field("buyer_city", "BT-52", "City", "text", "buyer"),
  field("buyer_postcode", "BT-53", "Postcode", "code", "buyer"),
  field("buyer_country_code", "BT-55", "Country", "country", "buyer"),
  field("buyer_email", "BT-58", "Email", "email", "buyer"),
  field("payment_terms", "BT-20", "Payment terms", "text", "payment"),
  field("payee_iban", "BT-84", "IBAN", "code", "payment"),
  field("payee_bic", "BT-86", "BIC", "code", "payment"),
  field("line_total", "BT-106", "Sum of lines", "amount", "totals"),
  field("allowance_total", "BT-107", "Allowances", "amount", "totals"),
  field("charge_total", "BT-108", "Charges", "amount", "totals"),
  field("net_total", "BT-109", "Net", "amount", "totals"),
  field("tax_total", "BT-110", "VAT", "amount", "totals"),
  field("gross_total", "BT-112", "Gross", "amount", "totals"),
  field("prepaid_amount", "BT-113", "Paid in advance", "amount", "totals"),
  field("payable_amount", "BT-115", "Amount due", "amount", "totals"),
] as const satisfies readonly FieldDef[];

export const GROUP_TITLES: Record<FieldDef["group"], string> = {
  invoice: "Invoice",
  supplier: "Supplier",
  buyer: "Buyer",
  payment: "Payment",
  totals: "Totals",
};

export interface FieldRowState {
  def: FieldDef;
  value: string | null;
  source: FieldSource;
  /** Only for values read by the LLM, or edited by a person; XML values carry none. */
  confidence: Confidence | null;
  evidence: string | null;
}

function asText(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  return String(value);
}

export function fieldRows(invoice: InvoiceDetail): FieldRowState[] {
  const source = (invoice.extraction_method ?? "manual") as FieldSource;
  const record = invoice as unknown as Record<string, unknown>;
  return FIELDS.map((def) => {
    const confidence = (invoice.field_confidence?.[def.name] ??
      null) as Confidence | null;
    return {
      def,
      value: asText(record[def.name]),
      source,
      // From the XML: no chip, the XML is the authority (design "From the XML").
      confidence:
        source === "xml" && confidence !== "edited" ? null : confidence,
      evidence: invoice.field_evidence?.[def.name] ?? null,
    };
  });
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const GERMAN_DATE = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const COUNTRY = /^[A-Z]{2}$/;
const TYPE_CODE = /^\d{3}$/;

/** A plain dot-decimal (`1190.00`, `1.234`) is read as written, as by the backend. */
const PLAIN_AMOUNT = /^\d+(?:\.\d+)?$/;
/** Digit groups split by dots, commas, spaces (also no-break and narrow) or apostrophes. */
const AMOUNT_BODY = /^\d+(?:[.,'’   ]\d+)*$/;
const AMOUNT_MARKS = /[.,'’   ]/;
const GROUP_ONLY_MARKS = new Set(["'", "’", " ", " ", " "]);

/** `body` as digit groups of three split by one kind of mark, or null if it is not that. */
function grouped(body: string, marks: ReadonlySet<string>): string | null {
  if (marks.size > 1 && ![...marks].every((mark) => GROUP_ONLY_MARKS.has(mark)))
    return null;
  const [first = "", ...rest] = body.split(AMOUNT_MARKS);
  if (rest.length === 0 || first.length > 3 || first.startsWith("0"))
    return null;
  if (rest.some((group) => group.length !== 3)) return null;
  return first + rest.join("");
}

/**
 * An amount typed in German or English style as the API's plain decimal (`1.190,00` →
 * `1190.00`), or null if it is not one. The same rules as the backend's reading of invoices
 * (docs/DECISIONS.md, "Extraction grading"): a plain dot-decimal is read as written; with
 * both `.` and `,` the last one is the decimal mark; spaces and apostrophes group thousands;
 * one mark used more than once groups thousands; one mark used once and followed by exactly
 * three digits (first group 1-3 digits, not starting with 0) groups thousands (`1,190` is
 * 1190); any other single mark is the decimal mark (`1190,5`).
 */
export function parseAmount(text: string): string | null {
  let value = text.trim().replace(/−/g, "-");
  const negative = value.startsWith("-");
  if (negative) value = value.slice(1).trim();
  const sign = negative ? "-" : "";
  if (PLAIN_AMOUNT.test(value)) return sign + value;
  if (!AMOUNT_BODY.test(value)) return null;
  const marks = [...value].filter((char) => !/\d/.test(char));
  const thousands = grouped(value, new Set(marks));
  if (thousands !== null) return sign + thousands;
  const last = marks.at(-1);
  if (last !== "." && last !== ",") return null;
  const at = value.lastIndexOf(last);
  const whole = value.slice(0, at);
  const fraction = value.slice(at + 1);
  const wholeMarks = new Set([...whole].filter((char) => !/\d/.test(char)));
  if (wholeMarks.has(last)) return null;
  const integer = wholeMarks.size ? grouped(whole, wholeMarks) : whole;
  return integer === null ? null : `${sign}${integer}.${fraction}`;
}

/** A date typed as YYYY-MM-DD or DD.MM.YYYY (also D.M.YYYY) as YYYY-MM-DD, or null. */
export function parseDate(text: string): string | null {
  const value = text.trim();
  let year: number, month: number, day: number;
  const german = GERMAN_DATE.exec(value);
  if (german) {
    [day, month, year] = german.slice(1).map(Number) as [
      number,
      number,
      number,
    ];
  } else if (DATE.test(value)) {
    [year, month, day] = value.split("-").map(Number) as [
      number,
      number,
      number,
    ];
  } else {
    return null;
  }
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day)
    return null;
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/** The inline error for a value typed into a field, or null when it can be saved. */
export function validate(def: FieldDef, raw: string): string | null {
  const value = raw.trim();
  if (value === "") return null; // clearing a field is allowed
  switch (def.kind) {
    case "date":
      return parseDate(value)
        ? null
        : "Enter the date as DD.MM.YYYY or YYYY-MM-DD.";
    case "amount": {
      const amount = parseAmount(value);
      return amount && !/\.\d{3,}$/.test(amount)
        ? null
        : "Enter an amount like 1.190,00 or 1190.00.";
    }
    case "email":
      return EMAIL.test(value) ? null : "Enter an email address.";
    case "country":
      return COUNTRY.test(value.toUpperCase())
        ? null
        : "Enter a two-letter country code.";
    case "typeCode":
      return TYPE_CODE.test(value)
        ? null
        : "Enter the three-digit type code, like 380.";
    default:
      return null;
  }
}

/** A valid typed value in the API's format: amounts `1190.00`, dates `YYYY-MM-DD`. */
function normalize(def: FieldDef, value: string): string {
  if (def.kind === "amount") return parseAmount(value) ?? value;
  if (def.kind === "date") return parseDate(value) ?? value;
  if (def.kind === "country") return value.toUpperCase();
  return value;
}

/** The PATCH body: only the fields whose value changed; empty text clears a field. */
export function changes(
  rows: readonly FieldRowState[],
  draft: Readonly<Record<string, string>>,
): Record<string, string | number | null> {
  const body: Record<string, string | number | null> = {};
  for (const row of rows) {
    if (!(row.def.name in draft)) continue;
    const typed = (draft[row.def.name] ?? "").trim();
    const next = typed === "" ? null : normalize(row.def, typed);
    if (next === row.value) continue;
    body[row.def.name] =
      next !== null && row.def.kind === "typeCode" ? Number(next) : next;
  }
  return body;
}

const MONEY = new Intl.NumberFormat("de-DE", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const DAY = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

export const CREDIT_NOTE = 381;

/** How a value reads; on a credit note (type 381) amounts carry a minus sign, as everywhere. */
export function display(
  def: FieldDef,
  value: string | null,
  currency = "EUR",
  creditNote = false,
): string {
  if (value === null) return "—";
  if (def.kind === "amount") {
    const stored = Number(value);
    const amount = creditNote && stored !== 0 ? -Math.abs(stored) : stored;
    if (Number.isNaN(amount)) return value;
    return currency === "EUR"
      ? `${MONEY.format(amount)} €`
      : `${MONEY.format(amount)} ${currency}`;
  }
  if (def.kind === "date" && DATE.test(value))
    return DAY.format(new Date(`${value}T00:00:00Z`));
  return value;
}

/** Fields shown in mono (codes, amounts, dates are not mono in the design). */
export function isMono(def: FieldDef): boolean {
  return def.kind === "code" || def.kind === "amount";
}

export const CONFIDENCE_TEXT: Record<Confidence, string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
  edited: "Edited",
};
