// Validate every site data file against its JSON Schema (site/data/schemas/). Exit 1 on any error.
//   node scripts/validate-data.mjs [data dir]     (default: public/data)
import Ajv2020 from "ajv/dist/2020.js";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const dir = process.argv[2] ?? "public/data";
const load = (p) => JSON.parse(readFileSync(p, "utf-8"));
const ajv = new Ajv2020({ allErrors: true, strict: false });
ajv.addSchema(load("data/schemas/files.schema.json"));

let errors = 0, count = 0;
for (const name of ["overview", "explorer", "scenarios", "disclosures", "methods", "pipeline"]) {
  const f = join(dir, `${name}.json`);
  if (!existsSync(f)) { console.error(`missing ${f}`); errors++; continue; }
  const ok = ajv.validate(`files.schema.json#/$defs/${name}`, load(f));
  if (!ok) { console.error(`${f}:`, ajv.errorsText(ajv.errors, { separator: "; " })); errors++; }
  count++;
}
console.log(`${count} data files checked, ${errors} invalid`);
process.exit(errors ? 1 : 0);
