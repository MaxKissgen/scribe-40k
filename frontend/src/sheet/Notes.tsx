/**
 * Note pages: the free text that is not part of the printed form.
 *
 * The paper has a box for everything the rules define and nothing for what actually gets
 * written down at a table -- what the party owes whom, who the contact on the Lathe Worlds
 * was, why not to go back to the Fifth Quadrant. Players attach that on loose sheets, and
 * a scan of a character sheet often ends with two pages of it.
 *
 * These pages are deliberately unstructured. There is nothing to extract into fields, so
 * the transcription is kept as written and shown as written. Each one exports as its own
 * continuation page after the sheet.
 */

import { TextField } from "../components/Field";
import { useSheet } from "../state";
import { PageFooter } from "./PageFooter";

interface NotePage {
  title: string | null;
  text: string | null;
  sourcePdfPage: number | null;
}

export function NotePages() {
  const { get, set, printMode } = useSheet();
  const pages = get<NotePage[]>("/notePages") ?? [];

  // On paper an empty note page is a blank sheet of paper, which is not worth printing.
  const visible = printMode
    ? pages.map((page, index) => ({ page, index })).filter(({ page }) => page.text?.trim())
    : pages.map((page, index) => ({ page, index }));

  if (printMode && visible.length === 0) return null;

  const removeAt = (index: number) =>
    set(
      "/notePages",
      pages.filter((_, i) => i !== index),
    );

  return (
    <>
      {visible.map(({ page, index }) => (
        <section className="page page--notes" key={index}>
          <header className="notes__header">
            <h2 className="block-heading">Notes</h2>
            <TextField
              pointer={`/notePages/${index}/title`}
              className="notes__title"
              placeholder="What these notes are"
            />
            {page.sourcePdfPage != null && (
              <span className="notes__source screen-only">
                transcribed from page {page.sourcePdfPage} of the scan
              </span>
            )}
            {!printMode && (
              <button
                type="button"
                className="repeat__remove"
                aria-label={`Remove note page ${index + 1}`}
                onClick={() => removeAt(index)}
              >
                ×
              </button>
            )}
          </header>

          <TextField
            pointer={`/notePages/${index}/text`}
            className="notes__body"
            multiline
            rows={30}
            placeholder="Anything that does not belong in a box on the sheet."
          />

          <PageFooter />
        </section>
      ))}

      {!printMode && (
        <section className="page page--collapsed page--notes-add">
          <button
            type="button"
            className="page__expand"
            onClick={() =>
              set("/notePages", [...pages, { title: null, text: null, sourcePdfPage: null }])
            }
          >
            + add a note page
          </button>
        </section>
      )}
    </>
  );
}
