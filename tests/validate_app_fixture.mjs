// Run with FRAME_APP_PROTOCOL pointing at the app's src/assistant/protocol.ts.
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';
if (!process.env.FRAME_APP_PROTOCOL) throw Error('Set FRAME_APP_PROTOCOL to the app protocol.ts path');
const {validateCapabilities,validateAnswer}=await import(pathToFileURL(process.env.FRAME_APP_PROTOCOL).href);
const data=JSON.parse(fs.readFileSync(process.argv[2] || 'tests/fixtures/assistant-package.json'));
let errors=0;
if (data.capabilities) {
 const caps=validateCapabilities(data.capabilities);
 for(const cap of caps.capabilities){try{validateAnswer(data.answers[cap.id],cap);console.log('PASS',cap.id)}catch(e){errors++;console.log('FAIL',cap.id,e.message)}}
} else {
 for(const [name,entry] of Object.entries(data)) {try {
  const cap=validateCapabilities({version:1,asOf:entry.answer.asOf,capabilities:[entry.capability]}).capabilities[0];
  validateAnswer(entry.answer,cap);console.log('PASS',name);
 } catch(e) {errors++;console.log('FAIL',name,e.message)}}
}
if(errors)process.exitCode=1;
