/**
 * Expandable sections.
 *
 * The paper has fixed line counts -- 21 gear lines, 3 ranged weapon boxes, 18 power boxes
 * -- but the schema's arrays are unbounded, and a character who outgrows the paper should
 * not be truncated by it. So the printed count is the *starting* size and the number of
 * rows the export will fit on the original layout, not a limit.
 *
 * Rows beyond the printed capacity are marked, so it is clear at a glance which ones will
 * spill onto a continuation page when the sheet is printed.
 */

import { useEffect, useState, type ReactNode } from "react";

import { useSheet } from "../state";
import type { Flag } from "../types";
import { FlagPopover } from "./FlagPopover";

interface RepeatProps<T> {
  /** Pointer to the array. */
  pointer: string;
  /** How many rows the paper has room for. */
  printedCapacity: number;
  /** A fresh, empty element. */
  blank: () => T;
  /** Render one row. */
  children: (item: T, index: number, itemPointer: string) => ReactNode;
  /** Label for the add button, e.g. "gear line". */
  noun: string;
  /** Keep at least this many rows visible even when empty. Defaults to the capacity. */
  minimumRows?: number;
  /**
   * Print the empty rows too, up to the printed capacity. True for the blocks the paper
   * prints as empty boxes you write into -- weapons, power boxes -- and false for the
   * ones that are simply lists, where blank rows would be noise.
   */
  printEmptyRows?: boolean;
  className?: string;
}

export function Repeat<T>({
  pointer,
  printedCapacity,
  blank,
  children,
  noun,
  minimumRows,
  printEmptyRows = false,
  className,
}: RepeatProps<T>) {
  const { get, set, printMode, flagsUnder } = useSheet();
  const items = (get<T[]>(pointer) ?? []) as T[];

  // Rows a re-read printout has that the sheet does not. They are shown where they would
  // land, as ghosts, and are not in the document until someone accepts them -- an update
  // that wrote itself into the sheet could overwrite a correct value with a plausible
  // misreading, which is the whole thing the suggestion mechanism exists to prevent.
  const suggested = printMode
    ? []
    : flagsUnder(`${pointer}/`)
        .filter((flag) => flag.rule === "update.added" && isDirectRow(pointer, flag.pointer))
        // By where they would land, not by how their pointers happen to sort: /gear/10
        // comes before /gear/9 as text, and reading a list out of order is confusing in a
        // way that a numeric sort costs nothing to avoid.
        .sort((a, b) => rowIndex(pointer, a.pointer) - rowIndex(pointer, b.pointer));

  // On screen the section always shows its printed shape, so an empty sheet still looks
  // like the paper. In print mode only real content is rendered -- blank placeholder rows
  // would waste a page.
  const visible = printMode
    ? printEmptyRows
      ? Math.max(items.length, minimumRows ?? printedCapacity)
      : items.length
    : Math.max(items.length, minimumRows ?? printedCapacity);

  // A placeholder row is one the paper prints but the document does not hold yet. Its
  // inputs must not write to `/gear/7/name` while the array has three items: the server
  // (rightly) refuses to invent structure, and the refusal used to block every later
  // save. So the moment a placeholder gets focus, the array is extended to reach it, and
  // only then does typing address a row that exists.
  const materialise = (index: number) => {
    if (index < items.length) return;
    set(pointer, padded(items, index + 1, blank));
  };

  const rows: ReactNode[] = [];
  for (let index = 0; index < visible; index += 1) {
    const item = items[index];
    const overflow = index >= printedCapacity;
    const placeholder = item === undefined;

    // A row that exists but holds nothing is not content. It printed anyway, and nine of
    // them in a row stacked their underlines into a solid black bar across the talents
    // block. It matters more than it used to: touching a placeholder row now creates a
    // real empty entry, so a sheet accumulates them just by being edited.
    if (printMode && !printEmptyRows && isBlank(item)) continue;

    rows.push(
      <div
        key={index}
        className={[
          "repeat__row",
          overflow ? "repeat__row--overflow" : "",
          placeholder ? "repeat__row--placeholder" : "",
        ]
          .filter(Boolean)
          .join(" ")}
        data-index={index}
        // Both paths, because focus is not guaranteed to arrive before the first
        // keystroke (a hidden window, an autofill, a paste). Capture-phase, so the row
        // exists before the input's own onChange writes into it.
        onFocusCapture={placeholder ? () => materialise(index) : undefined}
        onChangeCapture={placeholder ? () => materialise(index) : undefined}
      >
        {children(item, index, `${pointer}/${index}`)}
        {!printMode && item !== undefined && (
          <button
            type="button"
            className="repeat__remove"
            aria-label={`Remove ${noun} ${index + 1}`}
            onClick={() => set(pointer, items.filter((_, i) => i !== index))}
          >
            ×
          </button>
        )}
      </div>,
    );
  }

  return (
    <div className={`repeat ${className ?? ""}`}>
      {rows}
      {suggested.map((flag) => (
        <SuggestedRow key={flag.pointer} flag={flag} noun={noun} />
      ))}
      {!printMode && (
        <button
          type="button"
          className="repeat__add"
          onClick={() => set(pointer, [...items, blank()])}
        >
          + add {noun}
          {items.length >= printedCapacity && (
            <span className="repeat__note"> (continues on an extra page when printed)</span>
          )}
        </button>
      )}
    </div>
  );
}

/**
 * Ensures an array is at least `length` long without disturbing what is there.
 *
 * Used where the sheet prints a fixed set of lines the user types straight into -- disorder
 * lines, malignancy lines -- so the inputs exist before anything has been entered.
 */
export function padded<T>(items: T[] | undefined, length: number, blank: () => T): T[] {
  const result = [...(items ?? [])];
  while (result.length < length) result.push(blank());
  return result;
}

/** True for a row with nothing written in it, at any depth. */
function isBlank(item: unknown): boolean {
  if (item === null || item === undefined) return true;
  if (typeof item === "string") return item.trim() === "";
  if (typeof item === "boolean") return item === false;
  if (Array.isArray(item)) return item.every(isBlank);
  if (typeof item === "object") return Object.values(item).every(isBlank);
  return false;
}

/** True when `candidate` names a row of the list at `pointer`, not something deeper. */
function isDirectRow(pointer: string, candidate: string): boolean {
  const rest = candidate.slice(pointer.length + 1);
  return /^\d+$/.test(rest);
}

function rowIndex(pointer: string, candidate: string): number {
  return Number(candidate.slice(pointer.length + 1));
}

/**
 * A row the printout has and the sheet does not, shown where it would go.
 *
 * Deliberately not an editable row. Until it is accepted it is not in the document, so
 * there is nothing to edit -- and rendering it as a real row would invite exactly the
 * confusion this avoids, where a value that only exists as a proposal looks like data.
 */
function SuggestedRow({ flag, noun }: { flag: Flag; noun: string }) {
  const { registerField } = useSheet();
  const [open, setOpen] = useState(false);

  // Registering it keeps the flag anchored here rather than in the review bar's list of
  // flags with nowhere to go.
  useEffect(() => registerField(flag.pointer), [registerField, flag.pointer]);

  return (
    <div className="repeat__row repeat__row--suggested" data-pointer={flag.pointer}>
      <span className="repeat__suggestion">{describe(flag.expected)}</span>
      <button
        type="button"
        className="field__badge field__badge--suggested"
        aria-label={`A ${noun} the printout has and this sheet does not`}
        onClick={() => setOpen((value) => !value)}
      >
        +
      </button>
      {open && (
        <FlagPopover flags={[flag]} apply={() => {}} onClose={() => setOpen(false)} />
      )}
    </div>
  );
}

/** A row summarised in one line: what it says, in the order the sheet prints it. */
function describe(row: unknown): string {
  if (typeof row === "string") return row;
  if (row && typeof row === "object") {
    const parts = Object.values(row as Record<string, unknown>)
      .filter((value) => value !== null && value !== undefined && value !== "")
      .map((value) => (Array.isArray(value) ? value.join(", ") : String(value)))
      .filter(Boolean);
    if (parts.length) return parts.join(" — ");
  }
  return "(empty)";
}
