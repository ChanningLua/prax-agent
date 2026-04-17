#!/usr/bin/env node

const { execFileSync } = require("child_process");
const { resolve } = require("path");

const pythonCandidates = ["python3", "python"];
const pkgDir = resolve(__dirname, "..");

function findPython() {
  for (const cmd of pythonCandidates) {
    try {
      execFileSync(cmd, ["--version"], { stdio: "ignore" });
      return cmd;
    } catch {}
  }
  console.error(
    "Error: Python 3.10+ is required but not found.\n" +
      "Install it from https://www.python.org/downloads/"
  );
  process.exit(1);
}

const python = findPython();
const args = process.argv.slice(2);

try {
  execFileSync(python, ["-m", "prax", ...args], {
    cwd: process.cwd(),
    stdio: "inherit",
    env: {
      ...process.env,
      PYTHONPATH: pkgDir + (process.env.PYTHONPATH ? ":" + process.env.PYTHONPATH : ""),
    },
  });
} catch (e) {
  process.exit(e.status || 1);
}
