export type Block =
  | { type: "p"; text: string }
  | { type: "ul"; items: string[] }
  | { type: "table"; head: string[]; rows: string[][] };

/** Minimal block parser for the markdown subset the API produces (bold/italic inline, bullets, one table). */
export function parseBlocks(src: string): Block[] {
  const out: Block[] = [];
  const lines = src.split("\n");
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    if (l.trim().startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
        i++;
      }
      out.push({ type: "table", head: rows[0], rows: rows.slice(2) });
    } else if (/^\s*([-•]|\d+\.)\s/.test(l)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*([-•]|\d+\.)\s/.test(lines[i])) { items.push(lines[i].replace(/^\s*([-•]|\d+\.)\s/, "")); i++; }
      out.push({ type: "ul", items });
    } else {
      const buf: string[] = [];
      while (i < lines.length && lines[i].trim() && !lines[i].trim().startsWith("|") && !/^\s*([-•]|\d+\.)\s/.test(lines[i])) { buf.push(lines[i]); i++; }
      out.push({ type: "p", text: buf.join("\n") });
    }
  }
  return out;
}
