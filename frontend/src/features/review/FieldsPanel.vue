<script setup lang="ts">
// The Fields section of the review screen (HANDOFF "Invoice review", design "FieldRow" and
// "EvidencePopover"): every canonical field by group; values read by the LLM carry their
// confidence and the exact text they came from; XML values carry "From the XML". Editable
// only when the API allows `edit_fields`; a saved edit marks the field "Edited".
import { computed, nextTick, ref } from "vue";

import { api, ApiError, unwrap } from "../../api/client";
import type { components } from "../../api/schema";
import Button from "../../components/ui/Button.vue";
import ConfidenceBars from "../../components/ui/ConfidenceBars.vue";
import Icon from "../../components/ui/Icon.vue";
import { useToast } from "../../components/ui/useToast";
import {
  changes,
  CREDIT_NOTE,
  display,
  type FieldDef,
  type FieldRowState,
  fieldRows,
  GROUP_TITLES,
  type InvoiceDetail,
  isMono,
  validate,
} from "./fields";

type DocumentDetail = components["schemas"]["DocumentDetail"];

const props = defineProps<{ document: DocumentDetail; canEdit: boolean }>();
const emit = defineEmits<{ "select-field": [name: string]; saved: [] }>();

const { show } = useToast();
const invoice = computed(
  () => (props.document.invoice ?? null) as InvoiceDetail | null,
);
const rows = computed(() => (invoice.value ? fieldRows(invoice.value) : []));
const currency = computed(() => invoice.value?.currency ?? "EUR");
const creditNote = computed(() => invoice.value?.type_code === CREDIT_NOTE);
const fromXml = computed(() => invoice.value?.extraction_method === "xml");

const note = computed(() => {
  if (fromXml.value) {
    return props.canEdit
      ? "From the XML. The XML is the invoice."
      : "From the XML. Read-only: the XML is the invoice.";
  }
  if (invoice.value?.extraction_method === "llm") {
    return "Read by AI from the PDF text. Select a field to see where it came from.";
  }
  return "Typed in by hand.";
});

const groups = computed(() => {
  const result = new Map<FieldDef["group"], FieldRowState[]>();
  for (const row of rows.value) {
    // Empty fields of a full XML invoice add nothing; empty LLM/manual fields can be filled.
    if (row.value === null && fromXml.value && !props.canEdit) continue;
    const list = result.get(row.def.group) ?? [];
    list.push(row);
    result.set(row.def.group, list);
  }
  return [...result.entries()];
});

/** Who last edited each field, from the timeline (newest first). */
const editors = computed(() => {
  const found = new Map<string, string>();
  for (const event of props.document.events) {
    const data = event.data as { field?: unknown };
    if (
      event.type === "invoice.fields_edited" &&
      typeof data.field === "string"
    ) {
      if (!found.has(data.field))
        found.set(data.field, event.actor_name ?? "someone");
    }
  }
  return found;
});

// Evidence shows while the eye button is hovered or focused (HANDOFF), and stays open
// after a click until it is clicked again.
const evidenceOpen = ref<string | null>(null);
const previewing = ref<string | null>(null);
const shown = (name: string) =>
  evidenceOpen.value === name || previewing.value === name;
const editing = ref<string | null>(null);
const draft = ref("");
const fieldError = ref<string | null>(null);
const saving = ref(false);

function toggleEvidence(name: string): void {
  evidenceOpen.value = evidenceOpen.value === name ? null : name;
  emit("select-field", name);
}

async function startEdit(row: FieldRowState): Promise<void> {
  editing.value = row.def.name;
  draft.value = row.value ?? "";
  fieldError.value = null;
  evidenceOpen.value = null;
  await nextTick();
  document.getElementById(`edit-${row.def.name}`)?.focus();
}

function cancel(): void {
  editing.value = null;
  fieldError.value = null;
}

async function save(row: FieldRowState): Promise<void> {
  fieldError.value = validate(row.def, draft.value);
  if (fieldError.value) return;
  const body = changes([row], { [row.def.name]: draft.value });
  if (Object.keys(body).length === 0) {
    cancel();
    return;
  }
  saving.value = true;
  try {
    unwrap(
      await api.PATCH("/api/v1/documents/{document_id}/invoice", {
        params: { path: { document_id: props.document.id } },
        body: body as never,
      }),
    );
    editing.value = null;
    show({ kind: "success", message: `${row.def.label} saved.` });
    emit("saved");
  } catch (caught) {
    if (caught instanceof ApiError) {
      fieldError.value =
        caught.errors?.find((item) => item.path === row.def.name)?.message ??
        `${caught.title}. ${caught.detail}`;
    } else {
      fieldError.value = "The field could not be saved. Try again.";
    }
  } finally {
    saving.value = false;
  }
}

const LEVEL = { high: 3, medium: 2, low: 1 } as const;
const HINT = {
  high: "Matches the document.",
  medium: "Check it against the document.",
  low: "Check this value against the document.",
} as const;

/** The evidence snippet split around the value, so the value can be marked. */
function evidenceParts(row: FieldRowState): [string, string, string] | null {
  if (!row.evidence) return null;
  const value = row.value ?? "";
  const at = value
    ? row.evidence.toLowerCase().indexOf(value.toLowerCase())
    : -1;
  if (at < 0) return [row.evidence, "", ""];
  return [
    row.evidence.slice(0, at),
    row.evidence.slice(at, at + value.length),
    row.evidence.slice(at + value.length),
  ];
}
</script>

<template>
  <section class="fields" aria-labelledby="fields-title">
    <div class="fields__head">
      <h2 id="fields-title" class="fields__title">Fields</h2>
      <p class="fields__note">{{ note }}</p>
    </div>
    <p v-if="!invoice" class="fields__empty">No invoice data yet.</p>
    <div v-for="[group, list] in groups" :key="group" class="group">
      <h3 class="group__title">{{ GROUP_TITLES[group] }}</h3>
      <dl class="group__rows">
        <div
          v-for="row in list"
          :key="row.def.name"
          class="field"
          :class="{ 'field--low': row.confidence === 'low' }"
        >
          <dt class="field__label">
            <span class="field__term">{{ row.def.term }}</span>
            <span>{{ row.def.label }}</span>
          </dt>
          <dd class="field__value">
            <template v-if="editing === row.def.name">
              <form class="edit" @submit.prevent="save(row)">
                <label class="visually-hidden" :for="`edit-${row.def.name}`">
                  {{ row.def.label }}
                </label>
                <input
                  :id="`edit-${row.def.name}`"
                  v-model="draft"
                  class="edit__input"
                  :class="{ mono: isMono(row.def) }"
                  :aria-invalid="fieldError ? 'true' : undefined"
                  :aria-describedby="
                    fieldError ? `edit-error-${row.def.name}` : undefined
                  "
                  @keydown.esc.prevent="cancel"
                />
                <Button
                  type="submit"
                  size="sm"
                  variant="primary"
                  :loading="saving"
                  >Save</Button
                >
                <Button size="sm" variant="ghost" @click="cancel"
                  >Cancel</Button
                >
                <p
                  v-if="fieldError"
                  :id="`edit-error-${row.def.name}`"
                  class="edit__error"
                  role="alert"
                >
                  {{ fieldError }}
                </p>
              </form>
            </template>
            <template v-else>
              <span
                :class="{ mono: isMono(row.def), muted: row.value === null }"
              >
                {{ display(row.def, row.value, currency, creditNote) }}
              </span>
              <span
                v-if="row.confidence === 'edited'"
                class="chip chip--edited"
              >
                <Icon name="pencil" :size="12" />Edited by
                {{ editors.get(row.def.name) ?? "a person" }}
              </span>
              <span
                v-else-if="row.confidence"
                class="chip"
                :class="`chip--${row.confidence}`"
              >
                <ConfidenceBars
                  class="chip__bars"
                  :level="LEVEL[row.confidence]"
                  label=""
                />
                {{
                  row.confidence === "high"
                    ? "High"
                    : row.confidence === "medium"
                      ? "Medium"
                      : "Low"
                }}
              </span>
              <span class="field__actions">
                <button
                  v-if="row.evidence"
                  type="button"
                  class="icon-button"
                  :aria-label="`Show where ${row.def.label} came from`"
                  :title="`Show where ${row.def.label} came from`"
                  :aria-expanded="shown(row.def.name) ? 'true' : 'false'"
                  @click="toggleEvidence(row.def.name)"
                  @mouseenter="previewing = row.def.name"
                  @mouseleave="previewing = null"
                  @focus="previewing = row.def.name"
                  @blur="previewing = null"
                >
                  <Icon name="eye" :size="16" />
                </button>
                <button
                  v-if="canEdit"
                  type="button"
                  class="icon-button"
                  :aria-label="`Edit ${row.def.label}`"
                  :title="`Edit ${row.def.label}`"
                  @click="startEdit(row)"
                >
                  <Icon name="pencil" :size="16" />
                </button>
              </span>
              <p v-if="row.confidence === 'low'" class="field__hint">
                Low confidence. Check it against the document and correct it, or
                resolve the check with a note.
              </p>
              <div
                v-if="shown(row.def.name) && evidenceParts(row)"
                class="evidence"
                role="group"
                :aria-label="`Where ${row.def.label} came from`"
              >
                <p class="evidence__snippet mono">
                  {{ evidenceParts(row)?.[0]
                  }}<mark>{{ evidenceParts(row)?.[1] }}</mark
                  >{{ evidenceParts(row)?.[2] }}
                </p>
                <p
                  v-if="row.confidence && row.confidence !== 'edited'"
                  class="evidence__hint"
                >
                  {{ HINT[row.confidence] }}
                </p>
                <Button
                  size="sm"
                  variant="ghost"
                  @click="emit('select-field', row.def.name)"
                >
                  <Icon name="file-text" :size="16" />Show in document
                </Button>
              </div>
            </template>
          </dd>
        </div>
      </dl>
    </div>
  </section>
</template>

<style scoped>
.fields {
  min-width: 0;
  padding: var(--space-20);
  border-bottom: 1px solid var(--line);
}

@media (max-width: 767px) {
  .fields {
    padding: var(--space-16);
  }
}

.fields__head {
  margin-bottom: var(--space-12);
}

.fields__title {
  margin: 0;
  font-family: var(--font-head);
  font-size: var(--fs-16);
  font-weight: 700;
  letter-spacing: var(--tracking-head);
  color: var(--ink);
}

.fields__note,
.fields__empty {
  margin: var(--space-4) 0 0;
  color: var(--muted);
  font-size: var(--fs-13);
}

.group + .group {
  margin-top: var(--space-16);
}

.group__title {
  margin: 0 0 var(--space-4);
  color: var(--muted);
  font-size: var(--fs-12);
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.group__rows {
  margin: 0;
}

.field {
  display: grid;
  grid-template-columns: minmax(140px, 38%) 1fr;
  gap: var(--space-8);
  padding: var(--space-8);
  border-bottom: 1px solid var(--line);
}

.field--low {
  background: var(--block-soft);
}

/* --muted reaches only 4.4:1 on --block-soft; --muted-strong reaches 6:1 (WCAG 1.4.3). */
.field--low .field__label,
.field--low .muted {
  color: var(--muted-strong);
}

.field__label {
  display: grid;
  color: var(--muted);
  font-size: var(--fs-13);
}

.field__term {
  font-family: var(--font-mono);
  font-size: var(--fs-12);
}

.field__value {
  min-width: 0;
  overflow-wrap: anywhere;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-8);
  align-items: center;
  margin: 0;
}

.field__actions {
  display: inline-flex;
  gap: var(--space-4);
  margin-left: auto;
}

.field__hint {
  flex-basis: 100%;
  margin: 0;
  color: var(--block);
  font-size: var(--fs-12);
}

.mono {
  font-family: var(--font-mono);
}

.muted {
  color: var(--muted);
}

.chip {
  display: inline-flex;
  gap: var(--space-4);
  align-items: center;
  height: var(--size-badge);
  padding: 0 var(--space-8);
  border-radius: var(--r-pill);
  font-size: var(--fs-12);
  font-weight: 500;
}

.chip--high {
  background: var(--ok-soft);
  color: var(--ok);
}

.chip--medium {
  background: var(--warn-soft);
  color: var(--warn-text);
}

/* Text in --warn-text, the bars in --warn, as in StatusChip and SeverityChip. */
.chip--medium .chip__bars {
  color: var(--warn);
}

.chip--low {
  background: var(--block-soft);
  color: var(--block);
}

.chip--edited {
  background: var(--stamp-soft);
  color: var(--stamp);
}

.icon-button {
  display: inline-grid;
  place-items: center;
  width: var(--size-sm);
  height: var(--size-sm);
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--muted);
  cursor: pointer;
}

.icon-button:hover {
  background: var(--bar);
  color: var(--ink);
}

.evidence {
  flex-basis: 100%;
  padding: var(--space-8) var(--space-12);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--win);
}

.evidence__snippet {
  margin: 0;
  font-size: var(--fs-13);
}

.evidence__snippet mark {
  background: var(--hl);
  box-shadow: 0 0 0 2px var(--stamp);
}

.evidence__hint {
  margin: var(--space-4) 0 var(--space-8);
  color: var(--muted);
  font-size: var(--fs-12);
}

.edit {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-8);
  align-items: center;
  width: 100%;
}

.edit__input {
  flex: 1 1 160px;
  min-width: 0;
  height: var(--size-md);
  padding: 0 var(--space-8);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  font: inherit;
}

.edit__error {
  flex-basis: 100%;
  margin: 0;
  color: var(--block);
  font-size: var(--fs-12);
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
</style>
