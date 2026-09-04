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

import type { ReactNode } from "react";

import { useSheet } from "../state";

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
  const { get, set, printMode } = useSheet();
  const items = (get<T[]>(pointer) ?? []) as T[];

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
