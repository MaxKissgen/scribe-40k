/**
 * The editor's single source of state.
 *
 * Three responsibilities, kept together because they are entangled in practice:
 *
 * 1. Hold the character document, and apply edits optimistically so typing never lags on
 *    a round trip.
 * 2. Batch those edits and flush them to the server, which owns derivation and validation
 *    and hands back the finished document.
 * 3. Index the report's flags by pointer, so any field can ask "am I flagged?" in constant
 *    time rather than scanning a list on every render.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ApiError, api, type PatchOperation } from "./api";
import { resolve, withValue } from "./pointer";
import type {
  CharacterDocument,
  ExtractionReport,
  Flag,
  FlagStatus,
  Reference,
  UnmappedItem,
} from "./types";

/** How long to wait after the last keystroke before saving. */
const AUTOSAVE_DELAY_MS = 700;

export type SaveState = "idle" | "pending" | "saving" | "saved" | "error";

interface SheetContextValue {
  id: string;
  character: CharacterDocument;
  report: ExtractionReport | null;
  reference: Reference;

  /** Read the value at a pointer. */
  get: <T = unknown>(pointer: string) => T | undefined;
  /** Edit the value at a pointer. Applied at once, saved shortly after. */
  set: (pointer: string, value: unknown) => void;

  /** Open flags this field is answerable for. */
  flagsAt: (pointer: string) => Flag[];
  /** Open flags on this field or anything beneath it, for section-level badges. */
  flagsUnder: (prefix: string) => Flag[];

  /**
   * Tell the sheet that a control exists at this pointer. Returns the unregister.
   *
   * This is what lets a flag find a field even when it does not name one exactly: the
   * extractor reports uncertainty about `/skills/commonLore/specialisations/1`, an object,
   * and the control that can answer for it is the `.../1/subject` input.
   */
  registerField: (pointer: string) => () => void;
  /** The field a flag is shown on and writes to, or null if the sheet has none. */
  anchorOf: (flag: Flag) => string | null;

  openFlags: Flag[];
  /** Open flags with no field to show them on. Otherwise they would be unfixable. */
  orphanFlags: Flag[];
  reviewCount: number;
  resolveFlag: (flag: Flag, status: FlagStatus) => Promise<void>;
  /** Accept every open flag at once. Returns how many were cleared. */
  clearAllFlags: () => Promise<number>;

  tray: UnmappedItem[];
  assignFragment: (item: UnmappedItem, pointer: string) => Promise<void>;
  dismissFragment: (item: UnmappedItem) => Promise<void>;
  /** Turn a fragment into a note page, or start an empty one. */
  addNotePage: (item?: UnmappedItem) => Promise<void>;

  /** The field the review bar has jumped to, highlighted until focus moves on. */
  focusedPointer: string | null;
  focusPointer: (pointer: string | null) => void;

  saveState: SaveState;
  saveError: string | null;
  flush: () => Promise<void>;
  /** Save on demand: flush what is queued, or re-save the sheet if nothing is. */
  saveNow: () => Promise<void>;

  printMode: boolean;
}

const SheetContext = createContext<SheetContextValue | null>(null);

export function useSheet(): SheetContextValue {
  const value = useContext(SheetContext);
  if (!value) throw new Error("useSheet must be used inside <SheetProvider>");
  return value;
}

interface ProviderProps {
  id: string;
  initialCharacter: CharacterDocument;
  initialReport: ExtractionReport | null;
  reference: Reference;
  printMode?: boolean;
  children: ReactNode;
}

export function SheetProvider({
  id,
  initialCharacter,
  initialReport,
  reference,
  printMode = false,
  children,
}: ProviderProps) {
  const [character, setCharacter] = useState(initialCharacter);
  const [report, setReport] = useState(initialReport);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [focusedPointer, setFocusedPointer] = useState<string | null>(null);

  // Edits waiting to be sent. Keyed by pointer so that typing into one field repeatedly
  // collapses to a single operation rather than a queue of intermediate values.
  const pending = useRef(new Map<string, unknown>());
  const timer = useRef<number | null>(null);

  const flush = useCallback(async () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    if (pending.current.size === 0) return;

    const operations: PatchOperation[] = [...pending.current].map(([pointer, value]) => ({
      pointer,
      value,
    }));
    pending.current.clear();

    setSaveState("saving");
    try {
      const payload = await api.patch(id, operations);
      // The server's document is authoritative: it carries the derived values.
      setCharacter(payload.character);
      setReport(payload.report);
      setSaveState("saved");
      setSaveError(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const rejected = error instanceof ApiError && error.status >= 400 && error.status < 500;

      if (rejected) {
        // The server refused these edits outright. Re-queuing them would make every
        // later save fail the same way -- which is exactly what happened: one bad edit
        // silently blocked all saving until the page was reloaded. Drop them, and reload
        // what the server actually holds so the screen stops showing values that will
        // never be saved.
        setSaveState("error");
        setSaveError(`${message} — that edit was reverted`);
        try {
          const fresh = await api.get(id);
          setCharacter(fresh.character);
          setReport(fresh.report);
        } catch {
          /* leave the local state; the message already says saving failed */
        }
        return;
      }

      setSaveState("error");
      setSaveError(message);
      // A dropped connection: put the edits back so the next flush retries them rather
      // than losing the user's typing.
      for (const operation of operations) {
        if (!pending.current.has(operation.pointer)) {
          pending.current.set(operation.pointer, operation.value);
        }
      }
    }
  }, [id]);

  const set = useCallback(
    (pointer: string, value: unknown) => {
      setCharacter((current) => withValue(current, pointer, value));
      pending.current.set(pointer, value);
      setSaveState("pending");

      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => void flush(), AUTOSAVE_DELAY_MS);
    },
    [flush],
  );

  /**
   * What the save indicator does when clicked.
   *
   * Autosave already covers correctness, so this exists for the moment before closing a
   * tab when someone wants to see it happen rather than trust it. With edits queued it
   * skips the debounce; with none, it sends the sheet as it stands, which also re-runs
   * derivation and the checks on the server. Either way the click does something.
   */
  const saveNow = useCallback(async () => {
    if (pending.current.size > 0) {
      await flush();
      return;
    }
    setSaveState("saving");
    try {
      const payload = await api.replace(id, character);
      setCharacter(payload.character);
      setReport(payload.report);
      setSaveState("saved");
      setSaveError(null);
    } catch (error) {
      setSaveState("error");
      setSaveError(error instanceof Error ? error.message : String(error));
    }
  }, [id, character, flush]);

  // Never lose an edit to a closed tab.
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (pending.current.size > 0) {
        void flush();
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [flush]);

  const get = useCallback(
    <T,>(pointer: string) => resolve<T>(character, pointer),
    [character],
  );

  // Which pointers actually have a control on screen. Reference-counted, because two
  // controls can legitimately share one (a value and its printed twin during export).
  const fields = useRef(new Map<string, number>());
  const [fieldsVersion, setFieldsVersion] = useState(0);
  const versionBump = useRef<number | null>(null);

  const registerField = useCallback((pointer: string) => {
    fields.current.set(pointer, (fields.current.get(pointer) ?? 0) + 1);
    // Mounting a page registers hundreds of pointers; re-indexing on each one would be
    // hundreds of renders. One bump per batch is enough.
    if (versionBump.current === null) {
      versionBump.current = window.setTimeout(() => {
        versionBump.current = null;
        setFieldsVersion((value) => value + 1);
      }, 0);
    }
    return () => {
      const count = (fields.current.get(pointer) ?? 1) - 1;
      if (count > 0) fields.current.set(pointer, count);
      else fields.current.delete(pointer);
    };
  }, []);

  const openFlags = useMemo(
    () => (report?.flags ?? []).filter((flag) => flag.status === "needs_review"),
    [report],
  );

  // Where each open flag is shown. Most name their own field; the ones that do not are
  // the reason this exists, and the ones that match nothing at all have to be surfaced
  // some other way rather than silently held open forever.
  const anchors = useMemo(() => {
    void fieldsVersion;
    const present = fields.current;
    const all = [...present.keys()];
    const map = new Map<Flag, string | null>();
    for (const flag of openFlags) map.set(flag, findAnchor(flag.pointer, present, all));
    return map;
  }, [openFlags, fieldsVersion]);

  const flagIndex = useMemo(() => {
    const index = new Map<string, Flag[]>();
    for (const [flag, anchor] of anchors) {
      if (anchor === null) continue;
      const existing = index.get(anchor);
      if (existing) existing.push(flag);
      else index.set(anchor, [flag]);
    }
    return index;
  }, [anchors]);

  const orphanFlags = useMemo(
    () => openFlags.filter((flag) => anchors.get(flag) === null),
    [openFlags, anchors],
  );

  const flagsAt = useCallback((pointer: string) => flagIndex.get(pointer) ?? [], [flagIndex]);

  const anchorOf = useCallback((flag: Flag) => anchors.get(flag) ?? null, [anchors]);

  const flagsUnder = useCallback(
    (prefix: string) => openFlags.filter((flag) => flag.pointer.startsWith(prefix)),
    [openFlags],
  );

  const resolveFlag = useCallback(
    async (flag: Flag, status: FlagStatus) => {
      // Reflect it at once; the counter should not wait on the network.
      setReport((current) =>
        current
          ? {
              ...current,
              flags: current.flags.map((candidate) =>
                candidate.pointer === flag.pointer && candidate.rule === flag.rule
                  ? { ...candidate, status }
                  : candidate,
              ),
            }
          : current,
      );
      try {
        await api.resolveFlag(id, flag.pointer, flag.rule, status);
      } catch {
        // Put it back if the server disagreed.
        setReport(await api.report(id).catch(() => report));
      }
    },
    [id, report],
  );

  const clearAllFlags = useCallback(async () => {
    const { cleared } = await api.clearFlags(id);
    setReport(await api.report(id));
    return cleared;
  }, [id]);

  const tray = useMemo(
    () => (report?.unmapped ?? []).filter((item) => item.status === "unresolved"),
    [report],
  );

  const assignFragment = useCallback(
    async (item: UnmappedItem, pointer: string) => {
      await flush();
      const payload = await api.resolveUnmapped(id, item.id, "assigned", pointer);
      setCharacter(payload.character);
      setReport(payload.report);
    },
    [id, flush],
  );

  const dismissFragment = useCallback(
    async (item: UnmappedItem) => {
      const payload = await api.resolveUnmapped(id, item.id, "dismissed");
      setReport(payload.report);
    },
    [id],
  );

  const addNotePage = useCallback(
    async (item?: UnmappedItem) => {
      await flush();
      const payload = await api.addNotePage(id, item ? { fromUnmapped: item.id } : {});
      setCharacter(payload.character);
      setReport(payload.report);
    },
    [id, flush],
  );

  const value: SheetContextValue = {
    id,
    character,
    report,
    reference,
    get,
    set,
    flagsAt,
    flagsUnder,
    registerField,
    anchorOf,
    openFlags,
    orphanFlags,
    reviewCount: openFlags.length,
    resolveFlag,
    clearAllFlags,
    tray,
    assignFragment,
    dismissFragment,
    addNotePage,
    focusedPointer,
    focusPointer: setFocusedPointer,
    saveState,
    saveError,
    flush,
    saveNow,
    printMode,
  };

  return <SheetContext.Provider value={value}>{children}</SheetContext.Provider>;
}

/**
 * The field that answers for a flag.
 *
 * Extractors do not always name a leaf. A low-confidence reading of a whole
 * specialisation arrives as `/skills/commonLore/specialisations/1`; a schema error about
 * a missing key arrives on the object that lacks it. Neither has a control of its own,
 * and a flag with no control is a flag the user can see the count of and never clear.
 *
 * So: the field itself if there is one, else the nearest control above it, else the first
 * control inside it. Null means the sheet genuinely has nowhere to put it -- a flag about
 * the document as a whole, or one left over from a row that has since been deleted.
 */
function findAnchor(
  pointer: string,
  present: Map<string, number>,
  all: string[],
): string | null {
  if (present.has(pointer)) return pointer;
  if (pointer === "") return null;

  for (let cut = pointer.lastIndexOf("/"); cut > 0; cut = pointer.lastIndexOf("/", cut - 1)) {
    const ancestor = pointer.slice(0, cut);
    if (present.has(ancestor)) return ancestor;
  }

  // Shallowest first, so a flag on a gear row lands on its name rather than its notes.
  let best: string | null = null;
  const prefix = `${pointer}/`;
  for (const candidate of all) {
    if (!candidate.startsWith(prefix)) continue;
    if (best === null) best = candidate;
    else if (candidate.length < best.length) best = candidate;
    else if (candidate.length === best.length && candidate < best) best = candidate;
  }
  return best;
}
