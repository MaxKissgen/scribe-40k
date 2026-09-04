/**
 * The copyright line printed at the foot of every page of the original sheet.
 *
 * It belongs *inside* each page rather than once after the last one. Putting it after the
 * pages made it land on whichever page happened to be last -- page 3 for a non-psyker,
 * page 5 otherwise -- which changed that page's appearance and, with it, the fingerprint
 * used to recognise the sheet on re-import. Per page, it is both more faithful to the
 * paper and the same on every export.
 */
export function PageFooter() {
  return (
    <p className="page__footer">
      Permission granted to photocopy for personal use. © Games Workshop Ltd 2010.
    </p>
  );
}
