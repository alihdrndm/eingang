<script setup lang="ts">
// The Accuracy screen's content, shared by `/app/accuracy` and the public `/accuracy` (design
// "Eingang App Screens" ?screen=accuracy / accuracy-public). It renders `GET /accuracy` as
// published: headline "critical fields correct" per system with its 95 % interval, the per-field
// rates, validation parity with KoSIT, cost per invoice and the dataset. No sample numbers.
import { useQuery } from "@tanstack/vue-query";
import { computed } from "vue";

import { api, ApiError, unwrap } from "../../api/client";
import { queryKeys } from "../../api/query";
import EmptyState from "../../components/ui/EmptyState.vue";
import ErrorState from "../../components/ui/ErrorState.vue";
import Icon from "../../components/ui/Icon.vue";
import Skeleton, {
  type SkeletonColumn,
} from "../../components/ui/Skeleton.vue";
import {
  costPerInvoice,
  EVALS_URL,
  fieldKeys,
  fieldLabel,
  intervalText,
  percent,
  primarySystem,
  reportDate,
  type SystemResult,
  systemName,
} from "./accuracy";

const report = useQuery({
  queryKey: queryKeys.accuracy(),
  queryFn: async () => unwrap(await api.GET("/api/v1/accuracy")),
  staleTime: 5 * 60_000,
});

const data = computed(() => report.data.value ?? null);

const notPublished = computed(
  () =>
    report.error.value instanceof ApiError && report.error.value.status === 404,
);

const error = computed(() => {
  if (!report.isError.value || data.value || notPublished.value) return null;
  const value = report.error.value;
  if (value instanceof ApiError)
    return { title: value.title, detail: value.detail };
  return {
    title: "Couldn’t load the results",
    detail: value instanceof Error ? value.message : undefined,
  };
});

const primary = computed(() =>
  data.value ? primarySystem(data.value.systems) : null,
);
const others = computed<SystemResult[]>(() =>
  (data.value?.systems ?? []).filter(
    (system) => system.id !== primary.value?.id,
  ),
);
const fields = computed(() => fieldKeys(data.value?.systems ?? []));

const SKELETON_COLUMNS: SkeletonColumn[] = [
  { track: "minmax(0, 1fr)" },
  { track: "96px" },
  { track: "96px" },
  { track: "96px" },
];
</script>

<template>
  <div class="accuracy">
    <h1 id="accuracy-title" class="page-title">
      How accurately does Eingang read invoices?
    </h1>

    <Skeleton
      v-if="report.isPending.value"
      label="Loading the results"
      header
      :columns="SKELETON_COLUMNS"
      :rows="6"
    />
    <div v-else-if="notPublished" class="pane">
      <EmptyState
        icon="gauge"
        title="No results published yet"
        body="The numbers appear here after the first published test run. The method is already on GitHub."
      >
        <template #action>
          <a class="link" :href="EVALS_URL" target="_blank" rel="noopener"
            >Full method on GitHub<Icon name="external" :size="14"
          /></a>
        </template>
      </EmptyState>
    </div>
    <div v-else-if="error" class="pane">
      <ErrorState
        :title="error.title"
        :detail="error.detail"
        :retrying="report.isFetching.value"
        @retry="report.refetch()"
      />
    </div>

    <template v-else-if="data">
      <p class="note" role="note">
        <Icon name="info" :size="16" class="note-icon note-icon--info" />Results
        of the published test run of {{ reportDate(data.generated_at) }}, on
        {{ data.dataset.documents }} invoices.
      </p>

      <div class="top">
        <section class="pane" aria-labelledby="method-title">
          <div class="pane-head">
            <h2 id="method-title" class="pane-title">Method</h2>
          </div>
          <div class="pane-body">
            <ol class="steps">
              <li>
                <span class="step-n">1</span
                ><span
                  ><b>Corpus.</b> Public sample invoices, each a PDF with the
                  XML inside (ZUGFeRD and Factur-X).</span
                >
              </li>
              <li>
                <span class="step-n">2</span
                ><span
                  ><b>Ground truth.</b> The reader sees only the PDF; every
                  value is compared with the XML inside the same file.</span
                >
              </li>
              <li>
                <span class="step-n">3</span
                ><span
                  ><b>Baseline.</b> A regex reader runs on the same files, so
                  the AI has something to beat.</span
                >
              </li>
            </ol>
            <p class="small muted">
              Dataset {{ data.dataset.name }}: {{ data.dataset.documents }}
              documents, corpus commit
              <span class="mono">{{
                data.dataset.corpus_commit.slice(0, 7)
              }}</span
              >.
            </p>
            <a class="link" :href="EVALS_URL" target="_blank" rel="noopener"
              >Full method on GitHub<Icon name="external" :size="14"
            /></a>
          </div>
        </section>

        <section class="pane" aria-labelledby="headline-title">
          <div class="pane-head">
            <h2 id="headline-title" class="pane-title">
              Critical fields correct
            </h2>
          </div>
          <ul class="systems">
            <li v-for="system in data.systems" :key="system.id" class="system">
              <span class="system-name">{{ systemName(system) }}</span>
              <span class="headline mono">{{
                percent(system.critical_correct.value)
              }}</span>
              <span class="small muted">{{ intervalText(system) }}</span>
            </li>
          </ul>
          <p class="small muted critical">
            Critical fields: invoice number, invoice date, gross and, when the
            invoice has one, the IBAN. An invoice counts only when all of them
            are right.
          </p>
        </section>
      </div>

      <section v-if="primary" class="pane" aria-labelledby="fields-title">
        <div class="pane-head">
          <h2 id="fields-title" class="pane-title">Per field</h2>
          <span class="muted">{{ systemName(primary) }}</span>
        </div>
        <div class="table-wrap">
          <table class="fields">
            <caption class="sr-only">
              Per-field results of
              {{
                systemName(primary)
              }}
            </caption>
            <thead>
              <tr>
                <th scope="col">Field</th>
                <th scope="col" class="num">Correct</th>
                <th scope="col" class="num">Invented</th>
                <th scope="col" class="num">Missing</th>
                <th
                  v-for="system in others"
                  :key="system.id"
                  scope="col"
                  class="num"
                >
                  {{ systemName(system) }}, correct
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="key in fields" :key="key">
                <th scope="row">{{ fieldLabel(key) }}</th>
                <td class="num mono">
                  {{ percent(primary.fields[key]?.accuracy) }}
                </td>
                <td class="num mono">
                  {{ percent(primary.fields[key]?.hallucination) }}
                </td>
                <td class="num mono">
                  {{ percent(primary.fields[key]?.abstention) }}
                </td>
                <td v-for="system in others" :key="system.id" class="num mono">
                  {{ percent(system.fields[key]?.accuracy) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <dl class="legend">
          <dt>Correct</dt>
          <dd>The value matches the XML.</dd>
          <dt>Invented</dt>
          <dd>
            A value read where the XML has none. It may still be printed on the
            PDF.
          </dd>
          <dt>Missing</dt>
          <dd>Nothing read where the XML has a value.</dd>
          <dt>—</dt>
          <dd>Nothing to count: no invoice in the set fits the rate.</dd>
        </dl>
      </section>

      <div class="bottom">
        <section class="pane" aria-labelledby="parity-title">
          <div class="pane-head">
            <h2 id="parity-title" class="pane-title">
              Agreement with the official KoSIT validator
            </h2>
          </div>
          <div class="pane-body">
            <template v-if="data.parity">
              <span class="figure mono">{{
                percent(data.parity.verdict_agreement)
              }}</span>
              <span class="muted"
                >of verdicts agree;
                {{ percent(data.parity.rule_set_agreement) }} of the fired rule
                sets agree.</span
              >
              <span class="small muted"
                >{{ data.parity.files }} files compared,
                {{ data.parity.excluded }} excluded.</span
              >
            </template>
            <span v-else class="muted">Not measured in this run.</span>
          </div>
        </section>
        <section class="pane" aria-labelledby="cost-title">
          <div class="pane-head">
            <h2 id="cost-title" class="pane-title">AI cost per invoice</h2>
          </div>
          <div class="pane-body">
            <span v-for="system in data.systems" :key="system.id" class="cost"
              ><span class="figure mono">{{ costPerInvoice(system) }}</span
              ><span class="muted">{{ systemName(system) }}</span></span
            >
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped src="../approvals/pane.css"></style>
<style scoped>
.accuracy {
  display: grid;
  gap: var(--space-16);
  max-width: 1080px;
  min-width: 0;
}

.top,
.bottom {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-16);
  align-items: start;
}

.steps {
  display: grid;
  gap: var(--space-12);
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: var(--fs-14);
  color: var(--text);
}

.steps li {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  gap: var(--space-12);
}

.steps b {
  color: var(--ink);
  font-weight: 600;
}

.step-n {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: var(--r-pill);
  background: var(--stamp-soft);
  color: var(--stamp);
  font: 500 var(--fs-12) / 1 var(--font-mono);
}

.link {
  justify-self: start;
  display: inline-flex;
  align-items: center;
  gap: var(--space-4);
  color: var(--stamp);
  font-size: var(--fs-13);
  font-weight: 500;
}

.systems {
  display: grid;
  margin: 0;
  padding: 0;
  list-style: none;
}

.system {
  display: grid;
  gap: var(--space-4);
  padding: var(--space-16) var(--space-20);
  border-bottom: 1px solid var(--line);
}

.system-name {
  color: var(--ink);
  font-size: var(--fs-14);
  font-weight: 500;
}

.headline {
  color: var(--ink);
  font-size: var(--fs-section);
  font-weight: 500;
  line-height: var(--lh-tight);
}

.critical {
  margin: 0;
  padding: var(--space-12) var(--space-20);
}

.table-wrap {
  overflow-x: auto;
}

.fields {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-13);
}

.fields th,
.fields td {
  height: var(--size-row);
  padding: 0 var(--space-12);
  border-bottom: 1px solid var(--line);
  text-align: left;
  white-space: nowrap;
}

.fields th:first-child {
  padding-left: var(--space-20);
}

.fields thead th {
  height: var(--size-row-header);
  background: var(--bar);
  color: var(--muted-strong);
  font-size: var(--fs-12);
  font-weight: 500;
}

.fields tbody th {
  color: var(--ink);
  font-weight: 500;
}

.fields .num {
  text-align: right;
}

.fields td.mono {
  color: var(--ink);
}

.legend {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: var(--space-4) var(--space-12);
  margin: 0;
  padding: var(--space-12) var(--space-20) var(--space-16);
  font-size: var(--fs-12);
  color: var(--muted-strong);
}

.legend dt {
  color: var(--ink);
  font-weight: 500;
}

.legend dd {
  margin: 0;
}

.figure {
  color: var(--ink);
  font-size: var(--fs-21);
  font-weight: 500;
}

.cost {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--space-8);
}

@media (max-width: 1023px) {
  .top,
  .bottom {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 767px) {
  .system,
  .critical,
  .legend {
    padding-right: var(--space-16);
    padding-left: var(--space-16);
  }
}
</style>
