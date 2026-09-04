/**
 * The persistent "N fields need review" bar, and the assignment tray.
 *
 * Between them these are what stop a flag from being missed on a five-page sheet: a count
 * that does not go away, and next/previous navigation that scrolls to the field and opens
 * its explanation.
 */

import { useState } from "react";

import { api } from "../api";
import { useSheet } from "../state";
import type { UnmappedItem } from "../types";

export function ReviewBar({ onExport }: { onExport: () => void }) {
  const { openFlags, reviewCount, focusedPointer, focusPointer, saveState, saveError, report } =
    useSheet();
  const [trayOpen, setTrayOpen] = useState(false);

  const errors = openFlags.filter((flag) => flag.severity === "error").length;
  const warnings = reviewCount - errors;
  const trayCount = (report?.unmapped ?? []).filter((item) => item.status === "unresolved").length;

  // Some flags are about the document rather than a field -- a sheet page missing from the
  // scan, a page that could not be transcribed. There is nothing to scroll to, so they get
  // their own line instead of being cycled through as if they were fields.
  const documentFlags = openFlags.filter((flag) => !flag.pointer);
  const fieldFlags = openFlags.filter((flag) => flag.pointer);

  const step = (delta: number) => {
    if (fieldFlags.length === 0) return;
    const current = fieldFlags.findIndex((flag) => flag.pointer === focusedPointer);
    const next = (current + delta + fieldFlags.length) % fieldFlags.length;
    // Clear first, so jumping to the same pointer twice still re-triggers the scroll.
    focusPointer(null);
    window.setTimeout(() => focusPointer(fieldFlags[next].pointer), 0);
  };

  return (
    <>
      <div className="reviewbar">
        <div className="reviewbar__status">
          {reviewCount === 0 ? (
            <span className="reviewbar__clear">Nothing needs review</span>
          ) : (
            <>
              <strong>{reviewCount}</strong> field{reviewCount === 1 ? "" : "s"} need review
              {errors > 0 && <span className="pill pill--error">{errors} error</span>}
              {warnings > 0 && <span className="pill pill--warning">{warnings} uncertain</span>}
            </>
          )}
        </div>

        {fieldFlags.length > 0 && (
          <div className="reviewbar__nav">
            <button type="button" onClick={() => step(-1)} aria-label="Previous flagged field">
              ‹
            </button>
            <button type="button" onClick={() => step(1)} aria-label="Next flagged field">
              Next ›
            </button>
          </div>
        )}

        {trayCount > 0 && (
          <button
            type="button"
            className="reviewbar__tray-toggle"
            onClick={() => setTrayOpen((open) => !open)}
          >
            {trayCount} unassigned
          </button>
        )}

        <div className="reviewbar__right">
          <SaveIndicator state={saveState} error={saveError} />
          <button type="button" className="button button--primary" onClick={onExport}>
            Export PDF
          </button>
        </div>
      </div>

      {documentFlags.length > 0 && (
        <ul className="reviewbar__document-flags">
          {documentFlags.map((flag) => (
            <li key={flag.rule + flag.message} className={`pill pill--${flag.severity}`}>
              {flag.message}
            </li>
          ))}
        </ul>
      )}

      {trayOpen && <AssignmentTray onClose={() => setTrayOpen(false)} />}
    </>
  );
}

function SaveIndicator({ state, error }: { state: string; error: string | null }) {
  const text = {
    idle: "",
    pending: "Unsaved changes",
    saving: "Saving...",
    saved: "Saved",
    error: `Save failed: ${error ?? "unknown"}`,
  }[state];

  return <span className={`save save--${state}`}>{text}</span>;
}

// --------------------------------------------------------------------------------------

/**
 * Text found on the sheet that no field could hold.
 *
 * The calibration scan produces four of these: a "+30 Deceive" scribbled in the margin,
 * a block of implants the player wrote into the psychic power boxes, and two pages of
 * session notes. None belongs in a field, and none should be silently thrown away.
 */
function AssignmentTray({ onClose }: { onClose: () => void }) {
  const { id, tray, assignFragment, dismissFragment } = useSheet();
  const [target, setTarget] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  return (
    <aside className="tray">
      <header className="tray__header">
        <h2>Found on the sheet, not assigned</h2>
        <button type="button" onClick={onClose} aria-label="Close tray">
          ×
        </button>
      </header>

      <p className="tray__intro">
        The extractor read these but could not place them in a field. Give one a field to go
        in, or dismiss it. Anything left here stays in the extraction report.
      </p>

      <ul className="tray__list">
        {tray.map((item) => (
          <TrayEntry
            key={item.id}
            item={item}
            characterId={id}
            target={target[item.id] ?? ""}
            busy={busy === item.id}
            onTargetChange={(value) => setTarget((current) => ({ ...current, [item.id]: value }))}
            onAssign={async () => {
              const pointer = target[item.id];
              if (!pointer) return;
              setBusy(item.id);
              try {
                await assignFragment(item, pointer);
              } finally {
                setBusy(null);
              }
            }}
            onDismiss={async () => {
              setBusy(item.id);
              try {
                await dismissFragment(item);
              } finally {
                setBusy(null);
              }
            }}
          />
        ))}
      </ul>
    </aside>
  );
}

interface TrayEntryProps {
  item: UnmappedItem;
  characterId: string;
  target: string;
  busy: boolean;
  onTargetChange: (value: string) => void;
  onAssign: () => void;
  onDismiss: () => void;
}

function TrayEntry({
  item,
  characterId,
  target,
  busy,
  onTargetChange,
  onAssign,
  onDismiss,
}: TrayEntryProps) {
  return (
    <li className="tray__item">
      <p className="tray__text">{item.text}</p>
      <p className="tray__where">
        {item.source.location ?? `page ${item.source.pdfPage}`}
        {item.reason && <> — {item.reason}</>}
      </p>

      <img
        className="tray__crop"
        src={api.pageImage(characterId, item.source.pdfPage, item.source.bbox ?? undefined)}
        alt={`Page ${item.source.pdfPage} of the scan`}
        loading="lazy"
      />

      <div className="tray__actions">
        <input
          type="text"
          className="tray__pointer"
          placeholder="/bio/quirk"
          value={target}
          onChange={(event) => onTargetChange(event.target.value)}
          aria-label="Field to assign this to"
        />
        <button type="button" disabled={busy || !target} onClick={onAssign}>
          Assign
        </button>
        <button type="button" disabled={busy} onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </li>
  );
}
