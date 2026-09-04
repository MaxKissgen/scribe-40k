/**
 * What a flag looks like when you click it.
 *
 * The important part is the crop: a picture of that exact region of the scan, beside the
 * field. Without it the user is being asked "is this right?" with no way to tell. With it
 * the question answers itself in a second.
 */

import { useEffect, useRef } from "react";

import { api } from "../api";
import { useSheet } from "../state";
import type { Flag } from "../types";

interface Props {
  flags: Flag[];
  onClose: () => void;
  /** What "clear it" writes into the field; supplied by the control that owns it. */
  clearValue?: unknown;
}

export function FlagPopover({ flags, onClose, clearValue = null }: Props) {
  const { id, set, resolveFlag } = useSheet();
  const element = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!element.current?.contains(event.target as Node)) onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose]);

  return (
    <div className="popover" ref={element} role="dialog">
      {flags.map((flag) => (
        <div key={flag.rule} className={`popover__flag popover__flag--${flag.severity}`}>
          <p className="popover__message">{flag.message}</p>

          {flag.confidence !== null && (
            <p className="popover__meta">
              Model confidence {Math.round(flag.confidence * 100)}%
            </p>
          )}

          {flag.expected !== null && flag.expected !== undefined && (
            <p className="popover__meta">
              Expected <code>{String(flag.expected)}</code>, sheet reads{" "}
              <code>{String(flag.actual)}</code>
              <button
                type="button"
                className="popover__inline-action"
                onClick={() => {
                  set(flag.pointer, flag.expected);
                  void resolveFlag(flag, "user_fixed");
                  onClose();
                }}
              >
                use {String(flag.expected)}
              </button>
            </p>
          )}

          {flag.evidence?.snippet && (
            <pre className="popover__snippet">{flag.evidence.snippet}</pre>
          )}

          {flag.evidence?.pdfPage != null && (
            <Evidence pdfPage={flag.evidence.pdfPage} bbox={flag.evidence.bbox} id={id} />
          )}

          {flag.alternatives.length > 0 && (
            <div className="popover__alternatives">
              <span className="popover__meta">Other readings:</span>
              {flag.alternatives.map((alternative) => (
                <button
                  key={alternative}
                  type="button"
                  className="popover__alternative"
                  onClick={() => {
                    set(flag.pointer, alternative);
                    void resolveFlag(flag, "user_fixed");
                    onClose();
                  }}
                >
                  {alternative}
                </button>
              ))}
            </div>
          )}

          <div className="popover__actions">
            <button
              type="button"
              className="popover__action popover__action--accept"
              onClick={() => {
                void resolveFlag(flag, "accepted");
                onClose();
              }}
            >
              Keep as is
            </button>
            <button
              type="button"
              className="popover__action popover__action--clear"
              title="The extractor read something that is not there. Empty the field."
              onClick={() => {
                set(flag.pointer, clearValue);
                void resolveFlag(flag, "user_fixed");
                onClose();
              }}
            >
              Wrong — clear it
            </button>
            <button
              type="button"
              className="popover__action"
              onClick={() => {
                void resolveFlag(flag, "dismissed");
                onClose();
              }}
            >
              Not a problem
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * The scan, beside the field.
 *
 * With a bounding box this is a tight crop of the handwriting, which is what makes a flag
 * answerable at a glance. Without one -- OCR providers do not all return coordinates --
 * a whole 200 dpi page shrunk into a popover is illegible and worse than nothing, so it
 * offers the full page in a new tab instead of pretending to be useful.
 */
function Evidence({
  pdfPage,
  bbox,
  id,
}: {
  pdfPage: number;
  bbox: [number, number, number, number] | null;
  id: string;
}) {
  const href = api.pageImage(id, pdfPage);

  if (!bbox) {
    return (
      <p className="popover__meta">
        <a href={href} target="_blank" rel="noreferrer" className="popover__page-link">
          Open page {pdfPage} of the scan ↗
        </a>{" "}
        (this reading came with no position on the page)
      </p>
    );
  }

  return (
    <a href={href} target="_blank" rel="noreferrer" title="Open the full page">
      <img
        className="popover__crop"
        src={api.pageImage(id, pdfPage, bbox)}
        alt={`The scanned sheet around this field, page ${pdfPage}`}
      />
    </a>
  );
}
