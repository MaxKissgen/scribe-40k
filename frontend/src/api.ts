import type {
  CharacterDocument,
  CharacterPayload,
  CharacterSummary,
  ExtractionReport,
  FlagStatus,
  ImportProposal,
  PageTarget,
  PendingImport,
  PendingUpdate,
  Reference,
  UnmappedStatus,
} from "./types";

/**
 * A failed request, carrying the status so callers can tell a rejected edit (4xx --
 * retrying will not help) from a dropped connection (retrying is exactly right).
 */
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* the body was not JSON; the status text will have to do */
    }
    throw new ApiError(detail, response.status);
  }

  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export interface PatchOperation {
  pointer: string;
  value: unknown;
}

export const api = {
  reference: () => request<Reference>("/api/reference"),

  list: () => request<CharacterSummary[]>("/api/characters"),

  get: (id: string) => request<CharacterPayload>(`/api/characters/${id}`),

  create: (name?: string) =>
    request<CharacterPayload>("/api/characters", {
      method: "POST",
      body: JSON.stringify({ name: name ?? null }),
    }),

  remove: (id: string) => request<void>(`/api/characters/${id}`, { method: "DELETE" }),

  /** The editor's autosave. The server derives, validates and returns the finished sheet. */
  patch: (id: string, operations: PatchOperation[]) =>
    request<CharacterPayload>(`/api/characters/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ operations }),
    }),

  replace: (id: string, character: CharacterDocument) =>
    request<CharacterPayload>(`/api/characters/${id}`, {
      method: "PUT",
      body: JSON.stringify(character),
    }),

  report: (id: string) => request<ExtractionReport>(`/api/characters/${id}/report`),

  resolveFlag: (id: string, pointer: string, rule: string, status: FlagStatus) =>
    request<{ reviewCount: number; updated: number }>(`/api/characters/${id}/flags`, {
      method: "POST",
      body: JSON.stringify({ pointer, rule, status }),
    }),

  /** Append a free-text page, optionally consuming an unassigned fragment. */
  addNotePage: (
    id: string,
    body: { title?: string; text?: string; fromUnmapped?: string },
  ) =>
    request<CharacterPayload>(`/api/characters/${id}/note-pages`, {
      method: "POST",
      body: JSON.stringify({
        title: body.title ?? null,
        text: body.text ?? null,
        fromUnmapped: body.fromUnmapped ?? null,
      }),
    }),

  resolveUnmapped: (
    id: string,
    unmappedId: string,
    status: UnmappedStatus,
    assignedTo?: string,
  ) =>
    request<CharacterPayload>(`/api/characters/${id}/unmapped`, {
      method: "POST",
      body: JSON.stringify({ id: unmappedId, status, assignedTo: assignedTo ?? null }),
    }),

  /**
   * Upload a scan and get back a *proposal*, not a character.
   *
   * The reasoning model has not run at this point. Confirming the page assignment is what
   * starts it, which is why this is two calls rather than one.
   */
  async importPdf(file: File, name?: string): Promise<ImportProposal> {
    const body = new FormData();
    body.append("file", file);
    const query = name ? `?name=${encodeURIComponent(name)}` : "";

    const response = await fetch(`/api/import${query}`, { method: "POST", body });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        detail = (await response.json()).detail ?? detail;
      } catch {
        /* not JSON */
      }
      throw new Error(detail);
    }
    return (await response.json()) as ImportProposal;
  },

  listImports: () => request<PendingImport[]>("/api/imports"),

  getImport: (id: string) => request<ImportProposal>(`/api/imports/${id}`),

  /** Map the pages as assigned. The expensive call. */
  confirmImport: (id: string, assignment?: Record<string, PageTarget>) =>
    request<CharacterPayload>(`/api/imports/${id}/confirm`, {
      method: "POST",
      body: JSON.stringify({ assignment: assignment ?? null }),
    }),

  cancelImport: (id: string) => request<void>(`/api/imports/${id}`, { method: "DELETE" }),

  /** Accept every flag still open, in one action. */
  clearFlags: (id: string) =>
    request<{ cleared: number; reviewCount: number }>(
      `/api/characters/${id}/flags/clear`,
      { method: "POST" },
    ),

  /**
   * URL of a page of the scan, optionally cropped to a bounding box.
   *
   * ``source`` names an update when the page belongs to a printout that was read back
   * into this character rather than to the original import. A suggestion is about
   * handwriting on *that* paper; cropping the original would show a page nobody wrote on.
   */
  pageImage(
    id: string,
    pdfPage: number,
    bbox?: [number, number, number, number],
    source?: string | null,
  ) {
    const base = source
      ? `/api/characters/${id}/updates/${source}/pages/${pdfPage}`
      : `/api/characters/${id}/pages/${pdfPage}`;
    if (!bbox) return base;
    const [x0, y0, x1, y1] = bbox;
    return `${base}?x0=${x0}&y0=${y0}&x1=${x1}&y1=${y1}`;
  },

  // -- updates: re-reading a character from a printout ---------------------------------

  listUpdates: (id: string) => request<PendingUpdate[]>(`/api/characters/${id}/updates`),

  /** Upload a marked-up printout. Stops at the page assignment, as an import does. */
  async startUpdate(id: string, file: File): Promise<ImportProposal & { updateId: string }> {
    const body = new FormData();
    body.append("file", file);

    const response = await fetch(`/api/characters/${id}/updates`, { method: "POST", body });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        detail = (await response.json()).detail ?? detail;
      } catch {
        /* not JSON */
      }
      throw new Error(detail);
    }
    return (await response.json()) as ImportProposal & { updateId: string };
  },

  getUpdate: (id: string, updateId: string) =>
    request<ImportProposal & { updateId: string }>(`/api/characters/${id}/updates/${updateId}`),

  /** Read it as assigned and describe how it differs. Writes nothing to the character. */
  confirmUpdate: (id: string, updateId: string, assignment?: Record<string, PageTarget>) =>
    request<CharacterPayload & { suggested: number }>(
      `/api/characters/${id}/updates/${updateId}/confirm`,
      { method: "POST", body: JSON.stringify({ assignment: assignment ?? null }) },
    ),

  cancelUpdate: (id: string, updateId: string) =>
    request<void>(`/api/characters/${id}/updates/${updateId}`, { method: "DELETE" }),
};
