
import * as path from "path";
import * as fs from "fs";

async function main() {
  const pluginPath = "/home/xpajonx/.config/openempiric-dev/packages/oem-knowledge/src/oem_knowledge/plugins/openempiric.ts";
  const hookName = "tui.prompt.append";
  const projectRoot = "/home/xpajonx/.config/openempiric-dev/scratch/test_tmp/test_project";
  const promptText = "implement calendar copy feature";
  
  const { OpenempiricPlugin } = require(pluginPath);
  
  const pluginInstance = await OpenempiricPlugin({}, {});
  
  const msgInput = { content: promptText };
  const msgOutput = {};
  await pluginInstance["tui.prompt.append"](msgInput, msgOutput);
  console.log("SUCCESS:" + JSON.stringify(msgInput));
}

main().catch(err => {
  console.error("DR_ERR", err);
  process.exit(1);
});
