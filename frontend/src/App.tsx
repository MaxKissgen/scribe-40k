/**
 * The application shell: a character list, an import flow, and the editor.
 *
 * Routing is by URL path so that the PDF exporter can navigate a headless browser
 * straight to `/print/<id>` and get the same components in print mode. That is the whole
 * mechanism by which the export cannot drift from what the editor shows.
 */

import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { ReviewBar } from "./components/ReviewBar";
import { ImportAssignment } from "./ImportAssignment";
import { NotePages } from "./sheet/Notes";
import { Page1 } from "./sheet/Page1";
import { Page2 } from "./sheet/Page2";
import { Page3 } from "./sheet/Page3";
import { PsychicPages } from "./sheet/Page4";
import { SheetProvider } from "./state";
import type {
  CharacterPayload,
  CharacterSummary,
  ImportProposal,
  PendingImport,
  Reference,
} from "./types";

type Route =
  | { name: "list" }
  | { name: "assign"; id: string }
  | { name: "edit"; id: string }
  | { name: "print"; id: string };

function routeFromLocation(): Route {
  const path = window.location.pathname;
  const edit = path.match(/^\/character\/([^/]+)$/);
  if (edit) return { name: "edit", id: edit[1] };
  const print = path.match(/^\/print\/([^/]+)$/);
  if (print) return { name: "print", id: print[1] };
  // An import that has been read but not confirmed. A route of its own so that reloading
  // the page, or coming back to the tab later, lands back on the assignment rather than
  // losing it.
  const assign = path.match(/^\/import\/([^/]+)$/);
  if (assign) return { name: "assign", id: assign[1] };
  return { name: "list" };
}

export function App() {
  const [route, setRoute] = useState<Route>(routeFromLocation);
  const [reference, setReference] = useState<Reference | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onPopState = () => setRoute(routeFromLocation());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    api.reference().then(setReference).catch((e) => setError(String(e)));
  }, []);

  const navigate = useCallback((path: string) => {
    window.history.pushState({}, "", path);
    setRoute(routeFromLocation());
  }, []);

  if (error) return <div className="app-error">Could not reach the server: {error}</div>;
  if (!reference) return <div className="app-loading">Loading…</div>;

  if (route.name === "list") return <CharacterList reference={reference} navigate={navigate} />;

  if (route.name === "assign") {
    return <AssignPages key={route.id} id={route.id} navigate={navigate} />;
  }

  return (
    <Editor
      key={route.id}
      id={route.id}
      reference={reference}
      printMode={route.name === "print"}
      navigate={navigate}
    />
  );
}

// --------------------------------------------------------------------------------------

function CharacterList({
  reference,
  navigate,
}: {
  reference: Reference;
  navigate: (path: string) => void;
}) {
  const [characters, setCharacters] = useState<CharacterSummary[] | null>(null);
  const [pending, setPending] = useState<PendingImport[]>([]);
  const [busy, setBusy] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.list().then(setCharacters).catch(() => setCharacters([]));
    api.listImports().then(setPending).catch(() => setPending([]));
  }, []);

  useEffect(refresh, [refresh]);

  const onImport = async (file: File) => {
    setBusy(true);
    setImportError(null);
    try {
      const proposal = await api.importPdf(file);
      navigate(`/import/${proposal.id}`);
    } catch (error) {
      setImportError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="shell">
      <header className="shell__header">
        <h1>scribe-40k</h1>
        <p className="shell__subtitle">Dark Heresy character sheets</p>
      </header>

      <div className="shell__actions">
        <label className={`button button--primary ${busy ? "button--busy" : ""}`}>
          {busy ? "Reading the sheet…" : "Import a scanned sheet"}
          <input
            type="file"
            accept="application/pdf"
            hidden
            disabled={busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void onImport(file);
            }}
          />
        </label>

        <button
          type="button"
          className="button"
          onClick={async () => {
            const payload = await api.create();
            navigate(`/character/${payload.id}`);
          }}
        >
          Start a blank sheet
        </button>
      </div>

      {busy && (
        <p className="shell__note">
          Classifying and transcribing the pages. Nothing is mapped into fields until you
          have confirmed which page is which.
        </p>
      )}
      {importError && <p className="shell__error">{importError}</p>}

      {pending.length > 0 && (
        <section className="pending">
          <h2 className="pending__heading">Waiting for you to say which page is which</h2>
          <ul className="pending__list">
            {pending.map((entry) => (
              <li key={entry.id} className="pending__item">
                <span>
                  {entry.sourceName} — {entry.pageCount} page
                  {entry.pageCount === 1 ? "" : "s"}, read but not mapped
                </span>
                <span className="pending__actions">
                  <button type="button" onClick={() => navigate(`/import/${entry.id}`)}>
                    Continue
                  </button>
                  <button
                    type="button"
                    onClick={async () => {
                      if (!window.confirm(`Discard the import of ${entry.sourceName}?`)) return;
                      await api.cancelImport(entry.id);
                      refresh();
                    }}
                  >
                    Discard
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {characters === null ? (
        <p className="shell__note">Loading…</p>
      ) : characters.length === 0 ? (
        <p className="shell__note">
          No characters yet. Import a scanned sheet, or start a blank one.
        </p>
      ) : (
        <ul className="character-list">
          {characters.map((character) => (
            <li key={character.id}>
              <button type="button" onClick={() => navigate(`/character/${character.id}`)}>
                <span className="character-list__name">{character.name}</span>
                <span className="character-list__career">{character.career ?? "—"}</span>
                {character.reviewCount > 0 && (
                  <span className="pill pill--warning">{character.reviewCount} to review</span>
                )}
              </button>
              <button
                type="button"
                className="character-list__delete"
                aria-label={`Delete ${character.name}`}
                onClick={async () => {
                  if (!window.confirm(`Delete ${character.name}? This cannot be undone.`)) return;
                  await api.remove(character.id);
                  refresh();
                }}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      <footer className="shell__footer">
        Sheet layout © Games Workshop Ltd 2010. Page size {reference.page.widthMm} ×{" "}
        {reference.page.heightMm} mm.
      </footer>
    </div>
  );
}

// --------------------------------------------------------------------------------------

/** Loads a pending import and hands it to the assignment screen. */
function AssignPages({ id, navigate }: { id: string; navigate: (path: string) => void }) {
  const [proposal, setProposal] = useState<ImportProposal | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getImport(id)
      .then(setProposal)
      .catch((problem) => setError(problem instanceof Error ? problem.message : String(problem)));
  }, [id]);

  if (error) return <div className="app-error">{error}</div>;
  if (!proposal) return <div className="app-loading">Loading the pages…</div>;

  return (
    <ImportAssignment
      proposal={proposal}
      onDone={(characterId) => navigate(`/character/${characterId}`)}
      onCancelled={() => navigate("/")}
    />
  );
}

// --------------------------------------------------------------------------------------

function Editor({
  id,
  reference,
  printMode,
  navigate,
}: {
  id: string;
  reference: Reference;
  printMode: boolean;
  navigate: (path: string) => void;
}) {
  const [payload, setPayload] = useState<CharacterPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get(id)
      .then(setPayload)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [id]);

  if (error) return <div className="app-error">{error}</div>;
  if (!payload) return <div className="app-loading">Loading sheet…</div>;

  return (
    <SheetProvider
      id={id}
      initialCharacter={payload.character}
      initialReport={payload.report}
      reference={reference}
      printMode={printMode}
    >
      <div className={printMode ? "sheet sheet--print" : "sheet"}>
        {!printMode && (
          <>
            <div className="editor-nav screen-only">
              <button type="button" onClick={() => navigate("/")}>
                ‹ All characters
              </button>
            </div>
            <ReviewBar onExport={() => window.open(`/print/${id}`, "_blank")} />
          </>
        )}

        <Page1 />
        <Page2 />
        <Page3 />
        <PsychicPages />
        <NotePages />
      </div>
    </SheetProvider>
  );
}
