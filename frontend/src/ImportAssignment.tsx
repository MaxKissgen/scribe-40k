/**
 * "Which page of your scan is which page of the sheet?"
 *
 * The step between reading a document and mapping it. The machine has already guessed --
 * from the page image, and failing that from the words OCR read on it -- and this is where
 * a person either agrees or fixes it in five seconds.
 *
 * It earns the extra click because the failure it prevents is unrecoverable. A page filed
 * as the wrong one is mapped by the wrong prompt into the wrong fields, and nothing
 * downstream can tell. A page filed as notes is not mapped at all: a whole sheet
 * photographed at an angle came back as three pages of notes and an empty character, which
 * is what put this screen here.
 */

import { useState } from "react";

import { api } from "./api";
import type { ImportProposal, PageProposal, PageTarget } from "./types";

const NOTES: PageTarget = "notes";
const SKIP: PageTarget = "skip";

function sameTarget(a: PageTarget, b: PageTarget): boolean {
  return a === b;
}

function isSheetSlot(target: PageTarget): target is number {
  return typeof target === "number";
}

export function ImportAssignment({
  proposal,
  onDone,
  onCancelled,
}: {
  proposal: ImportProposal;
  onDone: (characterId: string) => void;
  onCancelled: () => void;
}) {
  const [assignment, setAssignment] = useState<Record<number, PageTarget>>(() =>
    Object.fromEntries(proposal.pages.map((page) => [page.pdfPage, page.proposed])),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);

  const pages = new Map(proposal.pages.map((page) => [page.pdfPage, page]));
  const at = (target: PageTarget) =>
    proposal.pages
      .map((page) => page.pdfPage)
      .filter((pdfPage) => sameTarget(assignment[pdfPage], target));

  /**
   * Move a page onto a target.
   *
   * Every slot holds as many pages as you put in it, sheet pages included. That is not
   * laxity: a character with more gear than the printed lines hold spills onto a further
   * page when exported, so a scan of that printout has two pages where the form has one,
   * and they are read together as the one page they are. Order within a slot follows
   * position in the upload, which is the order they were printed in.
   */
  const assign = (pdfPage: number, target: PageTarget) => {
    setAssignment((current) =>
      sameTarget(current[pdfPage], target) ? current : { ...current, [pdfPage]: target },
    );
  };

  const assignedSheetPages = Object.values(assignment).filter(isSheetSlot).length;

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const payload = await api.confirmImport(
        proposal.id,
        Object.fromEntries(Object.entries(assignment)),
      );
      onDone(payload.id);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!window.confirm("Discard this import? The uploaded file is deleted.")) return;
    setBusy(true);
    try {
      await api.cancelImport(proposal.id);
    } finally {
      onCancelled();
    }
  };

  const partedPages: [number, number][] = Array.from(
    Object.values(assignment)
      .filter(isSheetSlot)
      .reduce(
        (counts, sheet) => counts.set(sheet, (counts.get(sheet) ?? 0) + 1),
        new Map<number, number>(),
      ),
  )
    .filter(([, count]) => count > 1)
    .sort(([a], [b]) => a - b);

  const slots: { target: PageTarget; label: string; hint: string }[] = [
    ...Array.from({ length: proposal.sheetPageCount }, (_, index) => ({
      target: index + 1 as PageTarget,
      label: `Sheet page ${index + 1}`,
      hint: SHEET_PAGE_HINTS[index] ?? "",
    })),
    {
      target: NOTES,
      label: "Note pages",
      hint: "Free text kept as written. Not read into any field.",
    },
    {
      target: SKIP,
      label: "Not part of this sheet",
      hint: "Left out entirely: blank backs, duplicates, someone else's page.",
    },
  ];

  return (
    <div className="shell assign">
      <header className="shell__header">
        <h1>Which page is which?</h1>
        <p className="shell__subtitle">
          {proposal.sourceName} — {proposal.pages.length} page
          {proposal.pages.length === 1 ? "" : "s"}. Drag a page onto the sheet page it is,
          or leave it where it has been put. Nothing has been read into fields yet.
        </p>
      </header>

      {partedPages.length > 0 && (
        <p className="shell__note">
          {partedPages.map(([sheet, count]) => `sheet page ${sheet} is covered by ${count} pages`)
            .join(", ")}
          . They will be read together as one page — which is what a list too long for its
          printed lines looks like once it has been printed.
        </p>
      )}
      {assignedSheetPages === 0 && (
        <p className="shell__error">
          No page is assigned to the sheet. Confirming now produces an empty character with
          everything kept as notes.
        </p>
      )}
      {error && <p className="shell__error">{error}</p>}

      <div className="assign__slots">
        {slots.map((slot) => (
          <Slot
            key={String(slot.target)}
            label={slot.label}
            hint={slot.hint}
            dragging={dragging !== null}
            onDrop={(pdfPage) => assign(pdfPage, slot.target)}
          >
            {at(slot.target).map((pdfPage, index, all) => (
              <PageCard
                key={pdfPage}
                characterId={proposal.id}
                page={pages.get(pdfPage)!}
                target={assignment[pdfPage]}
                slots={slots}
                // Only a sheet page has parts. Two note pages are two note pages.
                part={isSheetSlot(slot.target) && all.length > 1 ? [index + 1, all.length] : null}
                onDragStart={() => setDragging(pdfPage)}
                onDragEnd={() => setDragging(null)}
                onChange={(target) => assign(pdfPage, target)}
              />
            ))}
          </Slot>
        ))}
      </div>

      <div className="assign__actions">
        <button type="button" className="button" onClick={cancel} disabled={busy}>
          Discard
        </button>
        <button
          type="button"
          className={`button button--primary ${busy ? "button--busy" : ""}`}
          onClick={confirm}
          disabled={busy}
        >
          {busy ? "Reading the sheet…" : "Looks right — read it"}
        </button>
      </div>

      {busy && (
        <p className="shell__note">
          Mapping six sections with the reasoning model. This takes a minute or two.
        </p>
      )}
    </div>
  );
}

/** What each printed page carries, so a slot means something before anything is in it. */
const SHEET_PAGE_HINTS = [
  "Name, characteristics, skills, wounds, armour.",
  "Weapons, talents, gear, weapon training.",
  "Rank advances and experience.",
  "Psychic powers and the minor-powers table.",
  "The remaining psychic power boxes.",
];

function Slot({
  label,
  hint,
  dragging,
  onDrop,
  children,
}: {
  label: string;
  hint: string;
  dragging: boolean;
  onDrop: (pdfPage: number) => void;
  children: React.ReactNode;
}) {
  const [over, setOver] = useState(false);
  const count = Array.isArray(children) ? children.length : children ? 1 : 0;

  return (
    <section
      className={[
        "assign__slot",
        dragging ? "assign__slot--armed" : "",
        over ? "assign__slot--over" : "",
        count === 0 ? "assign__slot--empty" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        const pdfPage = Number(event.dataTransfer.getData("text/plain"));
        if (Number.isInteger(pdfPage)) onDrop(pdfPage);
      }}
    >
      <h2 className="assign__slot-label">{label}</h2>
      <p className="assign__slot-hint">{hint}</p>
      <div className="assign__slot-body">{children}</div>
    </section>
  );
}

/**
 * One page of the upload.
 *
 * Draggable, and also carries a select. Drag and drop is the quick way and a select is
 * the one that works with a keyboard, on a touchscreen, and when a drag goes astray.
 */
function PageCard({
  characterId,
  page,
  target,
  slots,
  part,
  onDragStart,
  onDragEnd,
  onChange,
}: {
  characterId: string;
  page: PageProposal;
  target: PageTarget;
  slots: { target: PageTarget; label: string }[];
  /** ``[n, of]`` when this sheet page arrived as several pages, else null. */
  part: [number, number] | null;
  onDragStart: () => void;
  onDragEnd: () => void;
  onChange: (target: PageTarget) => void;
}) {
  const [showText, setShowText] = useState(false);

  return (
    <article
      className="assign__page"
      draggable
      onDragStart={(event) => {
        event.dataTransfer.setData("text/plain", String(page.pdfPage));
        event.dataTransfer.effectAllowed = "move";
        onDragStart();
      }}
      onDragEnd={onDragEnd}
    >
      {page.hasImage ? (
        <img
          className="assign__thumb"
          src={api.pageImage(characterId, page.pdfPage)}
          alt={`Page ${page.pdfPage} of the upload`}
          loading="lazy"
          draggable={false}
        />
      ) : (
        <div className="assign__thumb assign__thumb--none">no image</div>
      )}

      <div className="assign__page-body">
        <strong>Page {page.pdfPage}</strong>
        {part && (
          <span className="assign__part">
            part {part[0]} of {part[1]} — read as one page
          </span>
        )}
        <Confidence page={page} />

        <select
          className="assign__select"
          value={String(target)}
          aria-label={`What page ${page.pdfPage} is`}
          onChange={(event) => {
            const value = event.target.value;
            onChange(value === "notes" || value === "skip" ? value : Number(value));
          }}
        >
          {slots.map((slot) => (
            <option key={String(slot.target)} value={String(slot.target)}>
              {slot.label}
            </option>
          ))}
        </select>

        {page.transcriptionPreview && (
          <button
            type="button"
            className="assign__peek"
            onClick={() => setShowText((value) => !value)}
          >
            {showText ? "Hide what was read" : "What was read"}
          </button>
        )}
        {showText && <pre className="assign__text">{page.transcriptionPreview}</pre>}
      </div>
    </article>
  );
}

/** Why the machine put this page here, in a few words. */
function Confidence({ page }: { page: PageProposal }) {
  if (page.matchedBy === "text") {
    return (
      <span className="assign__why">
        recognised from its text ({Math.round((page.textScore ?? 0) * 100)}% of the words)
      </span>
    );
  }
  if (page.matchScore >= 0.55) {
    return (
      <span className="assign__why">
        matched the layout ({Math.round(page.matchScore * 100)}%)
      </span>
    );
  }
  if (page.ink < 0.01) {
    return <span className="assign__why assign__why--weak">looks blank</span>;
  }
  return <span className="assign__why assign__why--weak">not recognised</span>;
}
