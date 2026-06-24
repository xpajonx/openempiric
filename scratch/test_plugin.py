import os
import subprocess
from pathlib import Path
import json
import shutil

tmp_path = Path("scratch/test_tmp")
shutil.rmtree(tmp_path, ignore_errors=True)
tmp_path.mkdir(parents=True, exist_ok=True)

project_dir = tmp_path / "test_project"
project_dir.mkdir()

plugin_path = Path(__file__).resolve().parent.parent / "packages" / "oem-knowledge" / "src" / "oem_knowledge" / "plugins" / "openempiric.ts"

driver_content = f"""
import * as path from "path";
import * as fs from "fs";

async function main() {{
  const pluginPath = "{str(plugin_path.resolve())}";
  const hookName = "tui.prompt.append";
  const projectRoot = "{str(project_dir.resolve())}";
  const promptText = "implement calendar copy feature";
  
  const {{ OpenempiricPlugin }} = require(pluginPath);
  
  const pluginInstance = await OpenempiricPlugin({{}}, {{}});
  
  const msgInput = {{ content: promptText }};
  const msgOutput = {{}};
  await pluginInstance["tui.prompt.append"](msgInput, msgOutput);
  console.log("SUCCESS:" + JSON.stringify(msgInput));
}}

main().catch(err => {{
  console.error("DR_ERR", err);
  process.exit(1);
}});
"""
driver_file = tmp_path / "driver.ts"
driver_file.write_text(driver_content, encoding="utf-8")

# Setup project layout (.oem dir)
(project_dir / ".oem").mkdir()

cmd_env = dict(os.environ)
cmd_env["OEM_PREFLIGHT_AUTOMATIC"] = "1"
# Force repo dir lookup to current repo
cmd_env["OPENEMPIRIC_DIR"] = str(Path(__file__).resolve().parent.parent.resolve())
if "VIRTUAL_ENV" in os.environ:
    cmd_env["PATH"] = str(Path(os.environ["VIRTUAL_ENV"]) / "bin") + os.pathsep + os.environ.get("PATH", "")

res = subprocess.run(
    ["npx", "tsx", str(driver_file)],
    capture_output=True,
    text=True,
    env=cmd_env
)
print("RETURNCODE:", res.returncode)
print("STDOUT:", res.stdout)
print("STDERR:", res.stderr)
