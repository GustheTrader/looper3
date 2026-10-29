// Copies non-TS assets that tsc does not emit (the versioned policy config) into dist/.
import { copyFileSync, mkdirSync } from "node:fs";
import path from "node:path";

const source = path.resolve("src", "policy", "looper3.policy.json");
const target = path.resolve("dist", "policy", "looper3.policy.json");

mkdirSync(path.dirname(target), { recursive: true });
copyFileSync(source, target);
console.log(`copied ${source} -> ${target}`);
