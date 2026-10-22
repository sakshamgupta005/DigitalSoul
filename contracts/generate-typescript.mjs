// Generates web/src/types/contracts.ts from the standalone JSON Schemas.
// Schemas are read only; this script never edits them. Run via `python contracts/generate.py`.
import { compileFromFile } from "json-schema-to-typescript";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const outFile = join(here, "..", "web", "src", "types", "contracts.ts");
const schemas = ["command", "error", "omniroute_hypothesis", "raw_event", "workflow"];

const banner = `/* eslint-disable */
/**
 * GENERATED FILE. Do not edit by hand.
 * Source: contracts/*.schema.json (JSON Schema Draft 2020-12).
 * Regenerate with: python contracts/generate.py
 */
`;

const bodies = [];
for (const name of schemas) {
  const body = await compileFromFile(join(here, `${name}.schema.json`), {
    bannerComment: "",
    style: { semi: false, singleQuote: false },
    cwd: here,
  });
  bodies.push(body.trim());
}

const exported = bodies.join("\n\n");
const names = [...exported.matchAll(/^export (?:type|interface) (\w+)/gm)].map((m) => m[1]);
const duplicates = names.filter((n, i) => names.indexOf(n) !== i);
if (duplicates.length > 0) {
  throw new Error(`Duplicate generated type names: ${[...new Set(duplicates)].join(", ")}`);
}

await mkdir(dirname(outFile), { recursive: true });
await writeFile(outFile, `${banner}\n${exported}\n`, "utf8");
console.log(`Wrote ${outFile} (${names.length} exported types).`);
