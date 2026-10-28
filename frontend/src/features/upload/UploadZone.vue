<script setup lang="ts">
// Uploading from the inbox (HANDOFF "Inbox", design "Upload"): the "Upload invoices" button
// opens the upload dialog; dragging files anywhere over the window shows the drop overlay.
// Up to 10 files at a time, PDF or XML, at most 4 MB each; refused files get their own row
// and reason. Every file is its own request, three at once (uploadQueue).
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import Button from "../../components/ui/Button.vue";
import Dialog from "../../components/ui/Dialog.vue";
import Icon from "../../components/ui/Icon.vue";
import UploadFileRow from "./UploadFileRow.vue";
import { createSender } from "./sendUpload";
import { createUploadQueue, type SendFile } from "./uploadQueue";

const props = withDefaults(defineProps<{ send?: SendFile }>(), {
  send: undefined,
});
const emit = defineEmits<{ uploaded: [documentId: string] }>();

const MAX_FILES = 10;
const MAX_BYTES = 4 * 1024 * 1024;

const queue = createUploadQueue(props.send ?? createSender(), (row) => {
  if (row.state === "done" && row.documentId) emit("uploaded", row.documentId);
});

const open = ref(false);
const dragging = ref(false);
const tooMany = ref(false);
const picker = ref<HTMLInputElement | null>(null);
let dragDepth = 0;

function kindOf(file: File): "pdf" | "xml" | null {
  const name = file.name.toLowerCase();
  if (file.type === "application/pdf" || name.endsWith(".pdf")) return "pdf";
  if (file.type.endsWith("/xml") || name.endsWith(".xml")) return "xml";
  return null;
}

/** Takes the first 10 files; refuses others' type or size before anything is sent. */
function addFiles(list: FileList | File[]): void {
  const files = Array.from(list);
  tooMany.value = files.length > MAX_FILES;
  const accepted: File[] = [];
  for (const file of files.slice(0, MAX_FILES)) {
    if (kindOf(file) === null) {
      queue.addRejected(file.name, file.size, {
        title: "Not a PDF or XML file.",
        detail: "Only PDF and XML invoices can be uploaded.",
      });
    } else if (file.size > MAX_BYTES) {
      queue.addRejected(file.name, file.size, {
        title: "Larger than 4 MB.",
        detail: "Upload a smaller file, for example a single invoice.",
      });
    } else {
      accepted.push(file);
    }
  }
  queue.add(accepted);
  open.value = true;
}

/** Opens the upload dialog (the inbox's empty state has its own Upload button). */
function openDialog(): void {
  open.value = true;
}

defineExpose({ open: openDialog });

function choose(): void {
  picker.value?.click();
}

function onPicked(event: Event): void {
  const input = event.target as HTMLInputElement;
  if (input.files?.length) addFiles(input.files);
  input.value = "";
}

function hasFiles(event: DragEvent): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes("Files");
}

function onDragEnter(event: DragEvent): void {
  if (!hasFiles(event)) return;
  event.preventDefault();
  dragDepth += 1;
  dragging.value = true;
}

function onDragOver(event: DragEvent): void {
  if (hasFiles(event)) event.preventDefault();
}

function onDragLeave(event: DragEvent): void {
  if (!hasFiles(event)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) dragging.value = false;
}

function onDrop(event: DragEvent): void {
  if (!hasFiles(event)) return;
  event.preventDefault();
  dragDepth = 0;
  dragging.value = false;
  if (event.dataTransfer?.files.length) addFiles(event.dataTransfer.files);
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape" && dragging.value) {
    dragDepth = 0;
    dragging.value = false;
  }
}

onMounted(() => {
  window.addEventListener("dragenter", onDragEnter);
  window.addEventListener("dragover", onDragOver);
  window.addEventListener("dragleave", onDragLeave);
  window.addEventListener("drop", onDrop);
  window.addEventListener("keydown", onKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener("dragenter", onDragEnter);
  window.removeEventListener("dragover", onDragOver);
  window.removeEventListener("dragleave", onDragLeave);
  window.removeEventListener("drop", onDrop);
  window.removeEventListener("keydown", onKeydown);
});

const announcement = computed(() => {
  const rows = queue.rows;
  const uploading = rows.filter(
    (row) => row.state === "uploading" || row.state === "queued",
  );
  if (uploading.length)
    return `Uploading ${uploading.length} of ${rows.length} files.`;
  const failed = rows.filter((row) => row.state === "failed").length;
  if (rows.length === 0) return "";
  return failed
    ? `${rows.length - failed} uploaded, ${failed} not uploaded.`
    : `${rows.length} uploaded.`;
});
</script>

<template>
  <Button variant="primary" @click="open = true">
    <Icon name="upload" :size="16" />Upload invoices
  </Button>
  <input
    ref="picker"
    class="picker"
    type="file"
    accept=".pdf,.xml,application/pdf,application/xml,text/xml"
    multiple
    tabindex="-1"
    aria-hidden="true"
    @change="onPicked"
  />

  <div
    v-if="dragging"
    class="overlay"
    role="dialog"
    aria-modal="true"
    aria-label="Drop files to upload"
  >
    <div class="overlay__box">
      <Icon name="upload" :size="32" />
      <p class="overlay__text">
        Drop invoices to stamp them in · PDF or XML, up to 4 MB each, 10 at a
        time
      </p>
    </div>
  </div>

  <Dialog v-model:open="open" title="Upload invoices" size="lg">
    <div class="drop">
      <Icon name="upload" :size="20" />
      <span class="drop__title">Drop invoices here, or choose them</span>
      <span class="drop__hint">PDF or XML, up to 4 MB each, 10 at a time.</span>
      <Button @click="choose"
        ><Icon name="file-text" :size="16" />Choose files</Button
      >
    </div>
    <p v-if="tooMany" class="too-many" role="alert">
      <Icon class="too-many__icon" name="triangle" :size="16" />Only the first
      10 files were added. Drop the rest in a second batch.
    </p>
    <ul v-if="queue.rows.length" class="rows" aria-label="Uploads">
      <UploadFileRow
        v-for="row in queue.rows"
        :key="row.id"
        :row="row"
        @remove="queue.dismiss(row.id)"
        @retry="queue.retry(row.id)"
      />
    </ul>
    <p class="visually-hidden" aria-live="polite">{{ announcement }}</p>
    <template #footer>
      <span class="footer__note">Processing carries on if you close this.</span>
      <Button @click="open = false">Close</Button>
    </template>
  </Dialog>
</template>

<style scoped>
.picker {
  display: none;
}

.overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: var(--space-40);
  background: var(--stamp-soft);
}

.overlay__box {
  display: grid;
  justify-items: center;
  gap: var(--space-12);
  max-width: 560px;
  padding: var(--space-40);
  border: 2px dashed var(--stamp);
  border-radius: var(--r-lg);
  color: var(--stamp);
  text-align: center;
}

.overlay__text {
  margin: 0;
  font-family: var(--font-head);
  font-size: var(--fs-21);
  font-weight: 700;
  text-wrap: balance;
}

.drop {
  display: grid;
  justify-items: center;
  gap: var(--space-8);
  padding: var(--space-24);
  border: 1px dashed var(--line);
  border-radius: var(--r-md);
  text-align: center;
}

.drop__title {
  font-weight: 600;
}

.drop__hint {
  color: var(--muted);
  font-size: var(--fs-13);
}

.too-many {
  display: flex;
  gap: var(--space-8);
  align-items: center;
  color: var(--warn-text);
}

.too-many__icon {
  color: var(--warn);
}

.rows {
  margin: var(--space-12) 0 0;
  padding: 0;
  list-style: none;
}

.footer__note {
  margin-right: auto;
  color: var(--muted);
  font-size: var(--fs-13);
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
