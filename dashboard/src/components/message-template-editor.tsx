"use client";

import { forwardRef, useImperativeHandle, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * Variables supported by the template engine (campaign/template.py).
 * `description` is shown in the tooltip; `example` is used in the live preview.
 */
const STANDARD_VARIABLES: Array<{
  token: string;
  description: string;
  example: string;
}> = [
  { token: "first_name", description: "Lead's first name (from CSV column `first_name`, `firstname`, or split from `name`)", example: "Sarah" },
  { token: "last_name",  description: "Lead's last name",  example: "Chen" },
  { token: "company",    description: "Lead's company (from CSV column `company`, `project`, or `project_name`)", example: "Acme Inc." },
  { token: "title",      description: "Lead's job title",  example: "Head of Growth" },
];

const LOW_FILL_THRESHOLD = 0.1;

const EXAMPLE_BY_TOKEN = Object.fromEntries(STANDARD_VARIABLES.map(v => [v.token, v.example]));

export interface MessageTemplateEditorProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  /** Show the 300-char LinkedIn limit counter (only for the connection note). */
  showCharLimit?: boolean;
  /** Minimum textarea height in px. */
  minHeight?: number;
  /** DOM id passed through to the textarea — useful for <label htmlFor>. */
  id?: string;
  /** Additional custom variable tokens (e.g. extra_data keys) to offer alongside the standard set. */
  customVariables?: string[];
  /** Example values for custom variables, shown in the live preview (keyed by lowercased token). */
  customVariableExamples?: Record<string, string>;
  /**
   * Fill-rate per token name (0–1). When provided:
   *  - chips show a % badge for any token below 100%
   *  - chips for 0% tokens get a red border
   *  - the live preview renders 0%-fill tokens as an "empty" marker
   */
  fillRates?: Record<string, number>;
  /** Hide the chip row (when the page renders one shared <VariableChips> bar). */
  hideChips?: boolean;
  /** Hide the "space before a variable" hint (show it once per page instead). */
  hideHint?: boolean;
  /** Called when the textarea gains focus (lets a shared chip bar target it). */
  onFocus?: () => void;
}

export interface MessageTemplateEditorHandle {
  focus: () => void;
  insertToken: (token: string) => void;
}

// ── Preview helpers ──────────────────────────────────────────────────────────

type PreviewSegment =
  | { kind: "text";   content: string }
  | { kind: "filled"; leading: string; example: string; token: string }
  | { kind: "empty";  leading: string; token: string };

function buildPreview(template: string, fillRates?: Record<string, number>, customVariableExamples?: Record<string, string>): PreviewSegment[] {
  const segs: PreviewSegment[] = [];
  // Mirror the Python regex: optional leading horizontal space + {{token}}
  const pattern = /([ \t]?)\{\{(\w+)\}\}/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(template)) !== null) {
    const textBefore = template.slice(lastIndex, match.index);
    if (textBefore) segs.push({ kind: "text", content: textBefore });

    const leading = match[1];           // space captured from template (may be "")
    const rawToken = match[2];
    const token = rawToken.toLowerCase();
    const rate = fillRates?.[token];
    const isEmpty = rate === 0;
    const example = EXAMPLE_BY_TOKEN[token] ?? customVariableExamples?.[token] ?? `[${rawToken}]`;

    // Compute the space that will be prepended to a resolved value — mirrors
    // the Python _with_space() logic so the preview matches actual rendering.
    let resolvedLeading = leading;
    if (!leading) {
      const charBefore = match.index > 0 ? template[match.index - 1] : "";
      if (charBefore && !/\s/.test(charBefore)) resolvedLeading = " ";
    }

    if (isEmpty) {
      segs.push({ kind: "empty", leading: resolvedLeading, token });
    } else {
      segs.push({ kind: "filled", leading: resolvedLeading, example, token });
    }

    lastIndex = match.index + match[0].length;
  }

  const rest = template.slice(lastIndex);
  if (rest) segs.push({ kind: "text", content: rest });
  return segs;
}

// ── Variable chips ───────────────────────────────────────────────────────────

export interface VariableChipsProps {
  onInsert: (token: string) => void;
  disabled?: boolean;
  customVariables?: string[];
  fillRates?: Record<string, number>;
  /** Template text(s) in use: low-fill variables they reference stay visible. */
  usedText?: string;
  label?: string;
}

/** Row of "+ {{token}}" chips with fill-rate badges. Low-fill (<10%) ones sit behind a toggle. */
export function VariableChips({ onInsert, disabled, customVariables, fillRates, usedText = "", label = "Insert:" }: VariableChipsProps) {
  const [showLowFill, setShowLowFill] = useState(false);
  const used = usedText.toLowerCase();

  const customChips = (customVariables ?? [])
    .map((t) => t.trim())
    .filter(Boolean)
    .filter((t) => !STANDARD_VARIABLES.some((v) => v.token === t));

  const isLowFill = (token: string) =>
    !!fillRates &&
    fillRates[token] !== undefined &&
    fillRates[token] < LOW_FILL_THRESHOLD &&
    !used.includes(`{{${token}}}`);

  const chips: Array<{ token: string; custom: boolean; description: string; example?: string }> = [
    ...STANDARD_VARIABLES.map((v) => ({ token: v.token, custom: false, description: v.description, example: v.example })),
    ...customChips.map((t) => ({ token: t, custom: true, description: "Custom variable from the lead's imported data (extra_data)." })),
  ];
  const visible = chips.filter((c) => showLowFill || !isLowFill(c.token));
  const hiddenCount = chips.filter((c) => isLowFill(c.token)).length;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {label && <span className="text-xs text-muted-foreground mr-1">{label}</span>}
      <TooltipProvider delayDuration={150}>
        {visible.map((c) => {
          const rate = fillRates?.[c.token];
          const isZero = rate === 0;
          return (
            <Tooltip key={c.token}>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className={cn(
                    "h-6 px-2 font-mono text-xs",
                    c.custom && "border-dashed",
                    isZero && "border-red-300 dark:border-red-700"
                  )}
                  // Keep focus in the textarea so the shared bar knows where to insert.
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => onInsert(c.token)}
                  disabled={disabled}
                >
                  + {`{{${c.token}}}`}
                  {rate !== undefined && rate < 1 && (
                    <span
                      className={cn(
                        "ml-1 text-[10px] font-mono px-0.5 rounded leading-none",
                        isZero
                          ? "bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400"
                          : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                      )}
                    >
                      {Math.round(rate * 100)}%
                    </span>
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-xs">
                <p className="text-xs font-medium">{`{{${c.token}}}`}</p>
                <p className="text-xs text-muted-foreground mt-0.5">{c.description}</p>
                {c.example && (
                  <p className="text-xs text-muted-foreground mt-1">
                    Example: <span className="font-medium text-foreground">{c.example}</span>
                  </p>
                )}
                {rate !== undefined && rate < 1 && (
                  <p className={cn("text-xs mt-1 font-medium", isZero ? "text-red-500" : "text-amber-500")}>
                    {isZero ? "No leads have this field, so it will always send as empty" : `${Math.round(rate * 100)}% of leads have this field`}
                  </p>
                )}
              </TooltipContent>
            </Tooltip>
          );
        })}
      </TooltipProvider>
      {hiddenCount > 0 && (
        <button
          type="button"
          className="text-xs text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
          onClick={() => setShowLowFill((v) => !v)}
        >
          {showLowFill ? "Hide low-fill" : `+${hiddenCount} under 10% fill`}
        </button>
      )}
    </div>
  );
}

/** One-line explanation of how blank variables render; show once per page. */
export function BlankVariableHint() {
  return (
    <p className="text-xs text-muted-foreground">
      A space before a variable is dropped when the field is blank:{" "}
      <code className="text-[10px]">Hi {"{{first_name}},"}</code> becomes{" "}
      <code className="text-[10px]">Hi,</code> not <code className="text-[10px]">Hi ,</code>.
    </p>
  );
}

// ── Component ────────────────────────────────────────────────────────────────

/**
 * Textarea + "insert variable" chip row + live preview.
 *
 * Clicking a chip inserts `{{token}}` at the cursor (with an automatic leading
 * space if the preceding character is not whitespace), then re-focuses so the
 * user can keep typing. The template engine consumes that leading space when a
 * variable resolves to empty, so "Hi {{first_name}}," becomes "Hi Sarah," or
 * "Hi," — never "Hi ,".
 */
export const MessageTemplateEditor = forwardRef<
  MessageTemplateEditorHandle,
  MessageTemplateEditorProps
>(function MessageTemplateEditor(
  { value, onChange, placeholder, disabled, showCharLimit, minHeight = 96, id, customVariables, customVariableExamples, fillRates, hideChips, hideHint, onFocus },
  ref,
) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [cursor, setCursor] = useState<number | null>(null);

  function insertAtCursor(text: string) {
    const ta = textareaRef.current;
    if (!ta) {
      onChange(value + text);
      return;
    }
    const activeStart = ta.selectionStart ?? cursor ?? value.length;
    const activeEnd   = ta.selectionEnd   ?? cursor ?? value.length;
    const before = value.slice(0, activeStart);
    const after  = value.slice(activeEnd);
    const next   = before + text + after;
    onChange(next);
    const nextCursor = activeStart + text.length;
    setCursor(nextCursor);
    requestAnimationFrame(() => {
      if (!textareaRef.current) return;
      textareaRef.current.focus();
      textareaRef.current.setSelectionRange(nextCursor, nextCursor);
    });
  }

  function insertToken(token: string) {
    const ta = textareaRef.current;
    const pos = ta
      ? (ta.selectionStart ?? cursor ?? value.length)
      : (cursor ?? value.length);
    const charBefore = pos > 0 ? value[pos - 1] : "";
    // Add a leading space only when the cursor is not already at whitespace /
    // start-of-string — the template engine will consume it if the var is empty.
    const needSpace = charBefore !== "" && !/\s/.test(charBefore);
    insertAtCursor(needSpace ? ` {{${token}}}` : `{{${token}}}`);
  }

  useImperativeHandle(ref, () => ({
    focus: () => textareaRef.current?.focus(),
    insertToken,
  }));

  const charCount = value.length;
  const overLimit      = showCharLimit && charCount > 300;
  const approachingLimit = showCharLimit && charCount > 250 && charCount <= 300;

  const previewSegs = value.trim() ? buildPreview(value, fillRates, customVariableExamples) : null;

  return (
    <div className="space-y-1.5">
      {!hideChips && (
        <VariableChips
          onInsert={insertToken}
          disabled={disabled}
          customVariables={customVariables}
          fillRates={fillRates}
          usedText={value}
        />
      )}

      {/* Textarea */}
      <textarea
        id={id}
        ref={textareaRef}
        className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm resize-y disabled:opacity-50"
        style={{ minHeight }}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onSelect={(e) => setCursor(e.currentTarget.selectionStart)}
        onBlur={(e) => setCursor(e.currentTarget.selectionStart)}
        onFocus={onFocus}
        placeholder={placeholder}
        disabled={disabled}
      />

      {/* Live preview */}
      {previewSegs && (
        <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground font-medium block mb-1">
            Preview
          </span>
          {previewSegs.map((seg, i) => {
            if (seg.kind === "text") {
              return <span key={i}>{seg.content}</span>;
            }
            if (seg.kind === "filled") {
              return (
                <span key={i}>
                  {seg.leading}
                  <mark className="bg-blue-100 dark:bg-blue-900/30 text-blue-800 dark:text-blue-200 rounded px-0.5 not-italic">
                    {seg.example}
                  </mark>
                </span>
              );
            }
            // empty
            return (
              <span key={i} title={`{{${seg.token}}} has no data for any lead`}>
                <span className="inline-flex items-center gap-0.5 text-amber-600 dark:text-amber-400 text-xs rounded px-1 bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-700">
                  ⚠ {`{{${seg.token}}}`}
                </span>
              </span>
            );
          })}
        </div>
      )}

      {/* Footer: hint + optional char count */}
      {(!hideHint || showCharLimit) && (
      <div className="flex justify-between text-xs">
        {!hideHint ? <BlankVariableHint /> : <span />}
        {showCharLimit && (
          <span
            className={cn(
              "ml-4 shrink-0",
              overLimit
                ? "text-red-500 font-medium"
                : approachingLimit
                ? "text-amber-500"
                : "text-muted-foreground"
            )}
          >
            {charCount} / 300 chars
            {overLimit && " (LinkedIn will reject this)"}
          </span>
        )}
      </div>
      )}
    </div>
  );
});
