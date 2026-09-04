/**
 * RFC 6901 JSON Pointers, matching `backend/scribe40k/pointer.py`.
 *
 * Pointers are the shared vocabulary between the two halves of the app: flags address
 * fields by pointer, the assignment tray targets pointers, and autosave sends pointers.
 * Every editable control on the sheet is identified by one.
 */

export function escapeToken(token: string): string {
  return token.replace(/~/g, "~0").replace(/\//g, "~1");
}

export function ptr(...tokens: (string | number)[]): string {
  return tokens.map((token) => `/${escapeToken(String(token))}`).join("");
}

function parse(pointer: string): string[] {
  if (pointer === "") return [];
  if (!pointer.startsWith("/")) throw new Error(`not a JSON Pointer: ${pointer}`);
  return pointer
    .slice(1)
    .split("/")
    .map((token) => token.replace(/~1/g, "/").replace(/~0/g, "~"));
}

export function resolve<T = unknown>(document: unknown, pointer: string): T | undefined {
  let current: any = document;
  for (const token of parse(pointer)) {
    if (current === null || current === undefined) return undefined;
    current = Array.isArray(current) ? current[Number(token)] : current[token];
  }
  return current as T;
}

/** Immutable set: returns a copy with `pointer` changed, sharing everything untouched. */
export function withValue<T>(document: T, pointer: string, value: unknown): T {
  const tokens = parse(pointer);
  if (tokens.length === 0) return value as T;

  const clone = (node: any, depth: number): any => {
    const token = tokens[depth];
    const last = depth === tokens.length - 1;

    if (Array.isArray(node)) {
      const copy = [...node];
      const index = token === "-" ? copy.length : Number(token);
      copy[index] = last ? value : clone(node[index], depth + 1);
      return copy;
    }

    const copy = { ...(node ?? {}) };
    copy[token] = last ? value : clone(copy[token], depth + 1);
    return copy;
  };

  return clone(document, 0);
}
