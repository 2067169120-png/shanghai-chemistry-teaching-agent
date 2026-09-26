import fs from "node:fs";
import process from "node:process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const sharp = require("sharp");

const [input, output] = process.argv.slice(2);
if (!input || !output) {
  throw new Error("usage: node render_svg_png.mjs input.svg output.png");
}
const svg = fs.readFileSync(input);
await sharp(svg, { density: 300 })
  .resize({ width: 1950, height: 900, fit: "contain", background: "white" })
  .flatten({ background: "white" })
  .png({ compressionLevel: 9, palette: false })
  .toFile(output);
