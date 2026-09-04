/**
 * The controls the sheet is built from.
 *
 * Every one of them is flag-aware, which is what makes the editor double as the review
 * surface. A field with an open flag renders its value as an unconfirmed *suggestion* --
 * italic, tinted, with accept and reject controls -- rather than as settled data. A field
 * without one looks like an ordinary input. The user only has to touch what is doubtful.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";

import { useSheet } from "../state";
import type { Flag } from "../types";
import { FlagPopover } from "./FlagPopover";

interface FieldShellProps {
  pointer: string;
  children: (props: { flagged: boolean; flags: Flag[] }) => ReactNode;
  className?: string;
}

/** Wraps a control with its flag badge, popover and highlight state. */
export function FieldShell({ pointer, children, className }: FieldShellProps) {
  const { flagsAt, focusedPointer, printMode } = useSheet();
  const [open, setOpen] = useState(false);
  const element = useRef<HTMLSpanElement>(null);

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
          {open && <FlagPopover flags={flags} onClose={() => setOpen(false)} />}
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
}

export function TextField({
  pointer,
  label,
  placeholder,
  multiline = false,
  className,
  width,
}: TextFieldProps) {
  const { get, set, printMode } = useSheet();
  const value = (get<string>(pointer) ?? "") as string;

  if (printMode) {
    return (
      <span className={`printed ${className ?? ""}`} style={{ width }}>
        {label && <span className="printed__label">{label}</span>}
        <span className="printed__value">{value}</span>
      </span>
    );
  }

  return (
    <FieldShell pointer={pointer} className={className}>
      {({ flagged }) => (
        <>
          {label && <label className="field__label">{label}</label>}
          {multiline ? (
            <textarea
              className={`field__input ${flagged ? "field__input--suggested" : ""}`}
              value={value}
              placeholder={placeholder}
              rows={3}
              style={{ width }}
              onChange={(event) => set(pointer, event.target.value || null)}
            />
          ) : (
            <input
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
    <FieldShell pointer={pointer}>
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
