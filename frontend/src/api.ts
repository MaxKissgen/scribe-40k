import type {
  CharacterDocument,
  CharacterPayload,
  CharacterSummary,
  ExtractionReport,
  FlagStatus,
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

  async importPdf(file: File, name?: string): Promise<CharacterPayload> {
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
    return (await response.json()) as CharacterPayload;
  },

  /** URL of a page of the original scan, optionally cropped to a bounding box. */
  pageImage(id: string, pdfPage: number, bbox?: [number, number, number, number]) {
    const base = `/api/characters/${id}/pages/${pdfPage}`;
    if (!bbox) return base;
    const [x0, y0, x1, y1] = bbox;
    return `${base}?x0=${x0}&y0=${y0}&x1=${x1}&y1=${y1}`;
  },
};
