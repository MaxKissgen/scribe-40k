/**
 * The controls the sheet is built from.
 *
 * Every one of them is flag-aware, which is what makes the editor double as the review
 * surface. A field with an open flag renders its value as an unconfirmed *suggestion* --
 * italic, tinted, with accept and reject controls -- rather than as settled data. A field
 * without one looks like an ordinary input. The user only has to touch what is doubtful.
 */

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { useSheet } from "../state";
import type { Flag } from "../types";
import { FlagPopover } from "./FlagPopover";

// --------------------------------------------------------------------------------------
// Fitting text to the space the paper gives it
// --------------------------------------------------------------------------------------

const FIT_MAX_PT = 9;
const FIT_MIN_PT = 5.5;
/** Below this the box is not laid out yet (or is broken); measuring it would lie. */
const FIT_MIN_BOX_PX = 12;

/**
 * Shrink an element's font until its text fits, down to a floor.
 *
 * The paper's boxes are small and fixed; "2d10+2 E" in a damage cell a quarter of a
 * weapon box wide does not fit at body size. Clipping it hides the value, wrapping breaks
 * the row, so the honest option is what a person with a pen would do: write smaller.
 *
 * A box narrower than a couple of characters is not a tight fit, it is a layout that has
 * not settled. Shrinking into it produced the opposite of the intended effect -- every
 * gear and talent line pinned at the 5.5pt floor because the measurement said 2px -- so
 * that case is left alone rather than "fitted" to nothing.
 */
export function fitText(element: HTMLElement, maxPt = FIT_MAX_PT, minPt = FIT_MIN_PT) {
  element.style.fontSize = "";
  if (element.clientWidth < FIT_MIN_BOX_PX) return;

  let size = maxPt;
  element.style.fontSize = `${size}pt`;
  // scrollWidth reports the full content width of an <input> as well as a block, so the
  // same loop serves the editor and the printed value.
  while (size > minPt && element.scrollWidth > element.clientWidth + 1) {
    size -= 0.5;
    element.style.fontSize = `${size}pt`;
  }
}

/**
 * Keep an element's text fitted as its value, its box and its font change.
 *
 * All three matter. The value is obvious. The box changes when a column is added or the
 * window is resized. The font changes once, invisibly, when the sheet's handwriting face
 * finishes loading -- and everything measured before that was measured in a fallback face
 * of a different width.
 */
function useFitText<T extends HTMLElement>(value: string) {
  const ref = useRef<T>(null);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;

    fitText(element);

    const observer = new ResizeObserver(() => fitText(element));
    observer.observe(element);

    let cancelled = false;
    void document.fonts?.ready.then(() => {
      if (!cancelled && ref.current) fitText(ref.current);
    });

    return () => {
      cancelled = true;
      observer.disconnect();
    };
  }, [value]);

  return ref;
}

/** A single-line input whose text shrinks to fit rather than overflowing. */
function FittedInput(props: React.InputHTMLAttributes<HTMLInputElement> & { value: string }) {
  const ref = useFitText<HTMLInputElement>(props.value);
  return <input ref={ref} {...props} />;
}

/** The printed counterpart: a span that shrinks its text the same way. */
function FittedText({ value, className }: { value: string; className?: string }) {
  const ref = useFitText<HTMLSpanElement>(value);
  return (
    <span ref={ref} className={className}>
      {value}
    </span>
  );
}

// --------------------------------------------------------------------------------------

interface FieldShellProps {
  pointer: string;
  children: (props: { flagged: boolean; flags: Flag[] }) => ReactNode;
  className?: string;
  /**
   * What "this is wrong, clear it" writes into the field. Depends on the control: null
   * for text and numbers, false for a checkbox, the resting level for a skill.
   */
  clearValue?: unknown;
  /**
   * How an accepted reading reaches the document. Defaults to writing it at ``pointer``,
   * which is right for every control whose pointer holds exactly what it shows. The
   * special-rules line is the exception: it edits a list through one text input, so a
   * chosen alternative has to be split rather than written whole.
   */
  apply?: (value: unknown, flag: Flag) => void;
}

/** Wraps a control with its flag badge, popover and highlight state. */
export function FieldShell({
  pointer,
  children,
  className,
  clearValue = null,
  apply,
}: FieldShellProps) {
  const { flagsAt, focusedPointer, printMode, registerField, set } = useSheet();
  const [open, setOpen] = useState(false);
  const element = useRef<HTMLSpanElement>(null);

  // Announce that this pointer has a control, so a flag that names an object -- or a key
  // inside one -- can still find its way to a field the user can act on.
  useEffect(() => registerField(pointer), [registerField, pointer]);

  const flags = flagsAt(pointer);
  const flagged = flags.length > 0;
  const focused = focusedPointer === pointer;

  // The review bar's "next" button scrolls here and opens the explanation, so the user
  // does not have to hunt for the field it jumped to.
  useEffect(() => {
    if (!focused || printMode) return;
    element.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    setOpen(true);
  }, [focused, printMode]);

  const severity = flags.some((f) => f.severity === "error") ? "error" : "warning";

  const classes = [
    "field",
    className,
    flagged ? `field--flagged field--${severity}` : "",
    focused ? "field--focused" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <span className={classes} ref={element} data-pointer={pointer}>
      {children({ flagged, flags })}
      {flagged && !printMode && (
        <>
          <button
            type="button"
            className="field__badge"
            aria-label={`${flags.length} issue(s) on this field`}
            onClick={() => setOpen((value) => !value)}
          >
            {severity === "error" ? "!" : "?"}
          </button>
          {open && (
            <FlagPopover
              flags={flags}
              apply={apply ?? ((value) => set(pointer, value))}
              clearValue={clearValue}
              onClose={() => setOpen(false)}
            />
          )}
        </>
      )}
    </span>
  );
}

// --------------------------------------------------------------------------------------

interface TextFieldProps {
  pointer: string;
  label?: string;
  placeholder?: string;
  multiline?: boolean;
  className?: string;
  width?: string;
  /** Visible lines when multiline. The sheet's own boxes want three; a note page wants a page. */
  rows?: number;
}

export function TextField({
  pointer,
  label,
  placeholder,
  multiline = false,
  className,
  width,
  rows = 3,
}: TextFieldProps) {
  const { get, set, printMode } = useSheet();
  const value = (get<string>(pointer) ?? "") as string;

  if (printMode) {
    return (
      <span className={`printed ${className ?? ""}`} style={{ width }}>
        {label && <span className="printed__label">{label}</span>}
        {multiline ? (
          <span className="printed__value printed__value--multiline">{value}</span>
        ) : (
          <FittedText value={value} className="printed__value" />
        )}
      </span>
    );
  }

  return (
    <FieldShell pointer={pointer} className={className} clearValue={null}>
      {({ flagged }) => (
        <>
          {label && <label className="field__label">{label}</label>}
          {multiline ? (
            <textarea
              className={`field__input ${flagged ? "field__input--suggested" : ""}`}
              value={value}
              placeholder={placeholder}
              rows={rows}
              style={{ width }}
              onChange={(event) => set(pointer, event.target.value || null)}
            />
          ) : (
            <FittedInput
              type="text"
              className={`field__input ${flagged ? "field__input--suggested" : ""}`}
              value={value}
              placeholder={placeholder}
              style={{ width }}
              onChange={(event) => set(pointer, event.target.value || null)}
            />
          )}
        </>
      )}
    </FieldShell>
  );
}

// --------------------------------------------------------------------------------------

interface NumberFieldProps {
  pointer: string;
  label?: string;
  className?: string;
  min?: number;
  max?: number;
  width?: string;
}

export function NumberField({ pointer, label, className, min, max, width }: NumberFieldProps) {
  const { get, set, printMode } = useSheet();
  const value = get<number | null>(pointer);

  if (printMode) {
    return (
      <span className={`printed ${className ?? ""}`} style={{ width }}>
        {label && <span className="printed__label">{label}</span>}
        <span className="printed__value">{value ?? ""}</span>
      </span>
    );
  }

  return (
    <FieldShell pointer={pointer} className={className}>
      {({ flagged }) => (
        <>
          {label && <label className="field__label">{label}</label>}
          <input
            type="number"
            className={`field__input field__input--number ${
              flagged ? "field__input--suggested" : ""
            }`}
            value={value ?? ""}
            min={min}
            max={max}
            style={{ width }}
            onChange={(event) => {
              const raw = event.target.value;
              set(pointer, raw === "" ? null : Number(raw));
            }}
          />
        </>
      )}
    </FieldShell>
  );
}

// --------------------------------------------------------------------------------------

interface CheckboxProps {
  pointer: string;
  label?: string;
  /** Printed information rather than a player mark: shown filled and not editable. */
  printedFilled?: boolean;
}

export function Checkbox({ pointer, label, printedFilled = false }: CheckboxProps) {
  const { get, set, printMode } = useSheet();
  const checked = printedFilled || Boolean(get<boolean>(pointer));

  if (printMode || printedFilled) {
    // A labelled box needs a row of its own; a bare box is just the square. Without the
    // wrapper the label spills out of the 11px tick and every checkbox in a column
    // overprints its neighbours.
    if (!label) {
      return (
        <span
          className={`tick ${checked ? "tick--on" : ""} ${printedFilled ? "tick--printed" : ""}`}
        />
      );
    }
    return (
      <span className="tickline">
        <span className={`tick ${checked ? "tick--on" : ""}`} />
        <span className="tick__label">{label}</span>
      </span>
    );
  }

  return (
    <FieldShell pointer={pointer} clearValue={false}>
      {() => (
        <label className="tick tick--editable">
          <input
            type="checkbox"
            checked={checked}
            onChange={(event) => set(pointer, event.target.checked)}
          />
          {label && <span className="tick__label">{label}</span>}
        </label>
      )}
    </FieldShell>
  );
}

// --------------------------------------------------------------------------------------

interface SelectFieldProps {
  pointer: string;
  options: { value: string; label: string }[];
  className?: string;
  allowEmpty?: boolean;
}

export function SelectField({ pointer, options, className, allowEmpty }: SelectFieldProps) {
  const { get, set, printMode } = useSheet();
  const value = (get<string>(pointer) ?? "") as string;

  if (printMode) {
    const match = options.find((option) => option.value === value);
    return <span className={`printed__value ${className ?? ""}`}>{match?.label ?? ""}</span>;
  }

  return (
    <FieldShell pointer={pointer} className={className}>
      {({ flagged }) => (
        <select
          className={`field__input field__input--select ${
            flagged ? "field__input--suggested" : ""
          }`}
          value={value}
          onChange={(event) => set(pointer, event.target.value || null)}
        >
          {allowEmpty && <option value="" />}
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      )}
    </FieldShell>
  );
}
