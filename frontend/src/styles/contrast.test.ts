import { describe, expect, it } from "vitest";

import tokens from "./tokens.css?raw";

// WCAG 1.4.3: text needs 4.5:1 against its background (every app text size is below the
// 18.66 px bold / 24 px threshold for large text). Icons and bars need 3:1 (WCAG 1.4.11).

function hex(name: string): string {
  const match = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6});`).exec(tokens);
  if (!match?.[1]) throw new Error(`No hex value for --${name} in tokens.css`);
  return match[1];
}

function luminance(colour: string): number {
  const [r, g, b] = [1, 3, 5].map((at) => {
    const channel = parseInt(colour.slice(at, at + 2), 16) / 255;
    return channel <= 0.03928
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(foreground: string, background: string): number {
  const [light, dark] = [luminance(hex(foreground)), luminance(hex(background))]
    .sort((a, b) => a - b)
    .reverse() as [number, number];
  return (light + 0.05) / (dark + 0.05);
}

const TEXT_PAIRS: [string, string, string][] = [
  ["warn-text", "warn-soft", "Medium confidence chip"],
  ["warn-text", "win", "Upload 'only the first 10 files' alert"],
  ["muted-strong", "block-soft", "Low-confidence field label"],
  ["block", "block-soft", "Low confidence chip"],
  ["ok", "ok-soft", "High confidence chip"],
  ["stamp", "stamp-soft", "Edited chip"],
  ["muted", "win", "Muted text on the window"],
  ["muted", "paper", "Muted text on paper"],
];

const ICON_PAIRS: [string, string, string][] = [
  ["warn", "warn-soft", "Medium confidence bars"],
  ["warn", "win", "Upload alert icon"],
];

describe("token contrast", () => {
  it.each(TEXT_PAIRS)(
    "--%s on --%s reaches 4.5:1 (%s)",
    (foreground, background) => {
      expect(ratio(foreground, background)).toBeGreaterThanOrEqual(4.5);
    },
  );

  it.each(ICON_PAIRS)(
    "--%s on --%s reaches 3:1 (%s)",
    (foreground, background) => {
      expect(ratio(foreground, background)).toBeGreaterThanOrEqual(3);
    },
  );
});
