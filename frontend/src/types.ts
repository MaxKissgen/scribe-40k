/**
 * Shapes mirroring the two JSON Schemas.
 *
 * Only what the editor actually reads is typed here. The character document is treated as
 * a structure to be addressed by JSON Pointer rather than one to be modelled field by
 * field, which keeps this file from becoming a second copy of the schema that can drift
 * from it.
 */

export type CharacteristicAbbrev =
  | "WS"
  | "BS"
  | "S"
  | "T"
  | "Ag"
  | "Int"
  | "Per"
  | "WP"
  | "Fel";

export type ProficiencyLevel = "Untrained" | "Basic" | "Trained" | "+10" | "+20";

export type Severity = "info" | "warning" | "error";
export type FlagStatus = "needs_review" | "user_fixed" | "accepted" | "dismissed";
export type UnmappedStatus = "unresolved" | "assigned" | "dismissed";

export interface Evidence {
  pdfPage: number | null;
  sheetPage: number | null;
  snippet: string | null;
  bbox: [number, number, number, number] | null;
}

export interface Flag {
  pointer: string;
  severity: Severity;
  rule: string;
  message: string;
  status: FlagStatus;
  confidence: number | null;
  expected: unknown;
  actual: unknown;
  alternatives: string[];
  evidence: Evidence | null;
}

export interface UnmappedItem {
  id: string;
  text: string;
  source: {
    pdfPage: number;
    sheetPage: number | null;
    location: string | null;
    bbox: [number, number, number, number] | null;
  };
  reason: string | null;
  status: UnmappedStatus;
  assignedTo: string | null;
}

export interface PageRecord {
  pdfPage: number;
  kind: "sheet" | "blank" | "unrecognised";
  sheetPage: number | null;
  textSource: "text_layer" | "ocr";
  note: string | null;
}

export interface SectionRecord {
  name: string;
  status: "ok" | "failed" | "skipped";
  error: string | null;
}

export interface ExtractionReport {
  version: 1;
  generatedAt: string;
  source: { filename: string; fileHash: string; pageCount: number } | null;
  models: Record<string, { provider: string; model: string; supportsVision: boolean | null }> | null;
  pages: PageRecord[];
  flags: Flag[];
  unmapped: UnmappedItem[];
  sections: SectionRecord[];
}

/** The character document. Addressed by pointer, so deliberately loose. */
export type CharacterDocument = Record<string, any>;

export interface CharacterPayload {
  id: string;
  character: CharacterDocument;
  report: ExtractionReport | null;
  reviewCount: number;
}

export interface CharacterSummary {
  id: string;
  name: string;
  career: string | null;
  updatedAt: string;
  reviewCount: number;
  hasReport: boolean;
}

/** Everything printed on the paper, served by the backend so it exists in one place. */
export interface Reference {
  characteristics: { key: string; label: string; abbreviation: CharacteristicAbbrev }[];
  skills: {
    key: string;
    label: string;
    characteristic: CharacteristicAbbrev;
    isBasic: boolean;
    isGroup: boolean;
    column: 1 | 2 | 3;
    writeInLines: number;
  }[];
  groupSkillExamples: Record<string, string[]>;
  proficiencyColumns: string[];
  armourLocations: { key: string; location: string; hitRoll: string }[];
  weaponTraining: {
    basicAndPistol: { key: string; label: string }[];
    melee: { key: string; label: string }[];
  };
  minorPowers: { name: string; threshold: number; focus: string; sustain: boolean }[];
  printedCapacity: Record<string, number>;
  printedRankBlocks: number;
  page: { widthMm: number; heightMm: number };
}
