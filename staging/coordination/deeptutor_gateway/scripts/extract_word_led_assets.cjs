// Extract documented source regions; no generative reconstruction or source edits.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const sharp = require('C:/Users/20671/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');
const root = path.resolve(__dirname, '../../../..');
const output = path.join(root, 'runtime/deeptutor_shchem/qa/word-led-source-assets-20260909-r2');
const source = path.join(root, 'runtime/deeptutor_shchem/qa/real-source-preparation-v15-20260909-r1/source-pages');
const regions = [
  ['book-061.png', 'book-figure-2-12.png', [200,350,270,320], '教材印刷56页 图2.12'],
  ['book-061.png', 'book-figure-2-13.png', [696,493,229,270], '教材印刷56页 图2.13'],
  ['book-061.png', 'book-definition.png', [422,987,532,216], '教材印刷56页 定义段'],
  ['book-063.png', 'book-strong-weak.png', [422,196,534,189], '教材印刷58页 强弱电解质定义'],
  ['book-063.png', 'book-figure-2-15.png', [477,727,427,110], '教材印刷58页 图2.15'],
];
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
(async () => {
  if (fs.existsSync(output)) throw Error('Asset directory already exists');
  fs.mkdirSync(output, {recursive:true});
  const manifest = [];
  for (const [file, name, box, locator] of regions) {
    const input = fs.readFileSync(path.join(source,file));
    const [left,top,width,height] = box;
    const out = await sharp(input).extract({left,top,width,height}).png().toBuffer();
    fs.writeFileSync(path.join(output,name),out);
    manifest.push({name,locator,source_page:file,source_page_sha256:sha(input),box,sha256:sha(out)});
  }
  fs.writeFileSync(path.join(output,'crop-manifest.json'), JSON.stringify(manifest,null,2));
  console.log(JSON.stringify(manifest));
})();
