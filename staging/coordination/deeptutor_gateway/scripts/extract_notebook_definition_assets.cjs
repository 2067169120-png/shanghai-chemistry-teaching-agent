// Copy inspected textbook regions without modifying the source pages.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const sharp = require('C:/Users/20671/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');
const root = path.resolve(__dirname, '../../../..');
const source = path.join(root, 'runtime/deeptutor_shchem/qa/source-led-preparation-20260909');
const output = path.join(root, 'runtime/deeptutor_shchem/qa/notebook-definition-assets-20260909');
const specs = [
  ['textbook-062.png', 'ionization-definition.png', [157,895,815,100], '教材印刷57页 电离定义段'],
  ['textbook-063.png', 'strong-weak-definition.png', [416,165,555,216], '教材印刷58页 强弱电解质定义段'],
];
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
(async () => {
  if(fs.existsSync(output)) throw Error('Retain existing assets; use a new directory');
  fs.mkdirSync(output, {recursive: true});
  const records = [];
  for(const [file,name,box,sourceLabel] of specs) {
    const original = fs.readFileSync(path.join(source,file));
    const [left,top,width,height] = box;
    const bytes = await sharp(original).extract({left,top,width,height}).png().toBuffer();
    fs.writeFileSync(path.join(output,name),bytes);
    records.push({name,source:sourceLabel,source_page:path.join(source,file),source_sha256:sha(original),box,sha256:sha(bytes),width,height});
  }
  fs.writeFileSync(path.join(output,'manifest.json'),JSON.stringify(records,null,2));
  console.log(JSON.stringify(records));
})();
