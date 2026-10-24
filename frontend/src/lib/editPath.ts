export type EditPath = readonly (string | number)[];

/**
 * Immutable deep set for JSON-shaped documents (the inline editor's draft).
 * Clones through JSON, which is lossless for GeneratedContent.
 */
export function setIn<T>(root: T, path: EditPath, value: unknown): T {
  const clone = JSON.parse(JSON.stringify(root)) as T;
  if (path.length === 0) return clone;
  let cursor = clone as unknown as Record<string, unknown>;
  for (let i = 0; i < path.length - 1; i++) {
    cursor = cursor[String(path[i])] as Record<string, unknown>;
  }
  cursor[String(path[path.length - 1])] = value;
  return clone;
}
