// The Timeline of the review screen: one human-readable entry per audit event, newest first
// (event types from backend/src/invoices/models.py `Event.Type`). IBAN values are never shown:
// the API leaves them out of edit events, and this file never prints an edited IBAN either.
import type { components } from "../../api/schema";
import type { IconName } from "../../components/ui/icons";
import { FIELDS } from "../review/fields";
import { formatDateTime, plural } from "./format";

type DocumentDetail = components["schemas"]["DocumentDetail"];
type Event = components["schemas"]["Event"];

export type Tone = "stamp" | "ok" | "block" | "warn" | "neutral";

export interface TimelineEntry {
  key: string;
  title: string;
  /** A second line: a quoted comment or note, or a short detail. */
  detail?: string;
  /** True when `detail` is something a person wrote (shown as a quote). */
  quote?: boolean;
  when: string;
  datetime?: string;
  icon: IconName;
  tone: Tone;
}

const STEP_LABELS: Record<string, string> = {
  detect: "Format detection started",
  validate: "Check against the e-invoice rules started",
  extract: "Reading the fields started",
  check: "Business checks started",
};

const FIELD_LABELS: Record<string, string> = Object.fromEntries(
  FIELDS.map((field) => [field.name, field.label]),
);

/** Fields whose values never appear in the activity. */
const HIDDEN_VALUES = new Set(["payee_iban"]);

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

function by(event: Event): string {
  return event.actor_name ? ` by ${event.actor_name}` : "";
}

/** "Checked: valid e-invoice", "Checked: invalid, 1 error (BR-DE-15)", "Read by AI: …". */
function processedTitle(document: DocumentDetail): {
  title: string;
  icon: IconName;
  tone: Tone;
} {
  const validation = document.validation;
  if (validation && validation.status !== "not_applicable") {
    if (validation.status === "invalid") {
      const rules = [
        ...new Set(
          validation.issues
            .filter((issue) => issue.severity === "fatal")
            .map((issue) => issue.rule_id),
        ),
      ];
      const shown = rules.slice(0, 3).join(", ");
      const count = plural(
        Math.max(validation.fatal_count, rules.length),
        "error",
      );
      return {
        title: `Checked: invalid, ${count}${shown ? ` (${shown})` : ""}`,
        icon: "octagon",
        tone: "block",
      };
    }
    if (validation.status === "warnings") {
      return {
        title: `Checked: valid e-invoice, ${plural(validation.warning_count, "warning")}`,
        icon: "triangle",
        tone: "warn",
      };
    }
    return {
      title: "Checked: valid e-invoice",
      icon: "circle-check",
      tone: "ok",
    };
  }
  const invoice = document.invoice;
  if (invoice?.extraction_method === "llm") {
    const confidences = Object.values(invoice.field_confidence ?? {});
    const low = confidences.filter((value) => value === "low").length;
    const format = (document.format_label ?? "PDF").toLowerCase();
    const parts = [
      `Read by AI: ${format}`,
      plural(confidences.length, "field"),
    ];
    if (low > 0) parts.push(`${low} low confidence`);
    return { title: parts.join(", "), icon: "file-text", tone: "neutral" };
  }
  return { title: "Processed", icon: "check", tone: "neutral" };
}

function checkMessage(
  document: DocumentDetail,
  checkId: unknown,
): string | undefined {
  return document.checks.find((check) => check.check_id === checkId)?.message;
}

function describe(
  event: Event,
  document: DocumentDetail,
): Omit<TimelineEntry, "key" | "when" | "datetime"> {
  const data = event.data ?? {};
  switch (event.type) {
    case "document.received":
      return { title: "Received", icon: "inbox", tone: "stamp" };
    case "document.duplicate_upload":
      return {
        title: `Uploaded again${by(event)}; kept as one invoice`,
        icon: "copy",
        tone: "neutral",
      };
    case "processing.started":
      return data.from === "failed"
        ? {
            title: `Processing started again${by(event)}`,
            icon: "refresh",
            tone: "neutral",
          }
        : { title: "Processing started", icon: "loader", tone: "neutral" };
    case "processing.step": {
      const note = text(data.note);
      if (note) return { title: note, icon: "info", tone: "neutral" };
      const step = text(data.step) ?? "";
      return {
        title: STEP_LABELS[step] ?? `Step started: ${step}`,
        icon: "clock",
        tone: "neutral",
      };
    }
    case "processing.failed":
      return {
        title: "Processing failed",
        detail: document.failure_reason,
        icon: "circle-alert",
        tone: "block",
      };
    case "processing.completed":
      return processedTitle(document);
    case "invoice.fields_edited": {
      const field = text(data.field) ?? "";
      const label = FIELD_LABELS[field] ?? field.replace(/_/g, " ");
      const showValues =
        !HIDDEN_VALUES.has(field) && ("old" in data || "new" in data);
      const shown = (value: unknown): string =>
        value === null || value === undefined || value === ""
          ? "empty"
          : String(value);
      return {
        title: `${label} edited${by(event)}`,
        detail: showValues
          ? `${shown(data.old)} → ${shown(data.new)}`
          : undefined,
        icon: "pencil",
        tone: "neutral",
      };
    }
    case "check.created":
      return {
        title: "Check raised",
        detail: checkMessage(document, data.check_id),
        icon: "triangle",
        tone: "warn",
      };
    case "check.resolved":
      return {
        title:
          data.check_id === "C15"
            ? `Accepted anyway${by(event)}`
            : `Check resolved${by(event)}`,
        detail: text(data.note),
        quote: text(data.note) !== undefined,
        icon: "circle-check",
        tone: "ok",
      };
    case "check.cleared": {
      // The check is gone (its finding no longer applies), so only its id and code remain.
      const code = text(data.code)?.toLowerCase().replace(/_/g, " ");
      return {
        title: "Check no longer applies",
        detail:
          [text(data.check_id), code].filter(Boolean).join(" · ") || undefined,
        icon: "circle",
        tone: "neutral",
      };
    }
    case "review.completed":
      return {
        title: `Marked reviewed${by(event)}`,
        icon: "eye",
        tone: "neutral",
      };
    case "approval.decided":
      return data.to === "rejected"
        ? {
            title: `Rejected${by(event)}`,
            detail: text(data.comment),
            quote: text(data.comment) !== undefined,
            icon: "ban",
            tone: "block",
          }
        : {
            title: `Approved${by(event)}`,
            detail: text(data.comment),
            quote: text(data.comment) !== undefined,
            icon: "circle-check",
            tone: "ok",
          };
    case "review.sent_back":
      return {
        title: `Sent back to review${by(event)}`,
        detail: text(data.comment),
        quote: text(data.comment) !== undefined,
        icon: "chevron-left",
        tone: "warn",
      };
    case "document.reopened":
      return {
        title: `Reopened${by(event)}`,
        icon: "refresh",
        tone: "neutral",
      };
    case "reminder.sent": {
      const number = typeof data.number === "number" ? ` ${data.number}` : "";
      return {
        title: `Reminder${number} sent to approvers`,
        icon: "clock",
        tone: "neutral",
      };
    }
    case "export.created":
      return { title: "Exported", icon: "archive", tone: "neutral" };
    case "document.deleted":
      return { title: `Deleted${by(event)}`, icon: "x", tone: "block" };
    default: {
      const words = event.type.replace(/[._]/g, " ");
      return {
        title: `${words.charAt(0).toUpperCase()}${words.slice(1)}${by(event)}`,
        icon: "circle",
        tone: "neutral",
      };
    }
  }
}

/** The "now" entry on top: what the invoice is waiting for. */
function pending(document: DocumentDetail): TimelineEntry | null {
  if (document.status === "needs_review") {
    return {
      key: "now",
      title: "Waiting for review",
      when: "Now",
      icon: "eye",
      tone: "warn",
    };
  }
  if (document.status === "awaiting_approval") {
    return {
      key: "now",
      title: "Waiting for approval",
      when: "Now",
      icon: "hourglass",
      tone: "stamp",
    };
  }
  return null;
}

/** Every event as a timeline entry, newest first, with the pending step on top. */
export function timelineEntries(document: DocumentDetail): TimelineEntry[] {
  const events = [...document.events].sort(
    (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
  );
  const entries = events.map((event, index) => ({
    key: `${event.created_at}-${index}`,
    when: formatDateTime(event.created_at),
    datetime: event.created_at,
    ...describe(event, document),
  }));
  const now = pending(document);
  return now ? [now, ...entries] : entries;
}
