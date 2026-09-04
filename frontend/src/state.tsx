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

  /** Open flags on this exact field. */
  flagsAt: (pointer: string) => Flag[];
  /** Open flags on this field or anything beneath it, for section-level badges. */
  flagsUnder: (prefix: string) => Flag[];

  openFlags: Flag[];
  reviewCount: number;
  resolveFlag: (flag: Flag, status: FlagStatus) => Promise<void>;

  tray: UnmappedItem[];
  assignFragment: (item: UnmappedItem, pointer: string) => Promise<void>;
  dismissFragment: (item: UnmappedItem) => Promise<void>;

  /** The field the review bar has jumped to, highlighted until focus moves on. */
  focusedPointer: string | null;
  focusPointer: (pointer: string | null) => void;

  saveState: SaveState;
  saveError: string | null;
  flush: () => Promise<void>;

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

  // Index once per report change rather than scanning per field per render.
  const flagIndex = useMemo(() => {
    const index = new Map<string, Flag[]>();
    for (const flag of report?.flags ?? []) {
      if (flag.status !== "needs_review") continue;
      const existing = index.get(flag.pointer);
      if (existing) existing.push(flag);
      else index.set(flag.pointer, [flag]);
    }
    return index;
  }, [report]);

  const openFlags = useMemo(
    () => (report?.flags ?? []).filter((flag) => flag.status === "needs_review"),
    [report],
  );

  const flagsAt = useCallback((pointer: string) => flagIndex.get(pointer) ?? [], [flagIndex]);

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

  const value: SheetContextValue = {
    id,
    character,
    report,
    reference,
    get,
    set,
    flagsAt,
    flagsUnder,
    openFlags,
    reviewCount: openFlags.length,
    resolveFlag,
    tray,
    assignFragment,
    dismissFragment,
    focusedPointer,
    focusPointer: setFocusedPointer,
    saveState,
    saveError,
    flush,
    printMode,
  };

  return <SheetContext.Provider value={value}>{children}</SheetContext.Provider>;
}
