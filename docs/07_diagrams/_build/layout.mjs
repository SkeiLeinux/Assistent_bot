import { layoutProcess } from 'bpmn-auto-layout';
import { readFileSync, writeFileSync, readdirSync } from 'fs';
const [,, srcDir, outDir] = process.argv;
for (const f of readdirSync(srcDir).filter(f => f.endsWith('.src.bpmn'))) {
  const xml = readFileSync(`${srcDir}/${f}`, 'utf8');
  const out = await layoutProcess(xml);
  const name = f.replace('.src.bpmn', '.bpmn');
  writeFileSync(`${outDir}/${name}`, out);
  console.log(`${name}: DI=${out.includes('BPMNDiagram')} shapes=${(out.match(/BPMNShape/g)||[]).length} len=${out.length}`);
}
