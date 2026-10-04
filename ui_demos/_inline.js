/* Inlines _harness.css and _harness.js into each demo, so every file opens
   standalone — no sibling fetches, which is what broke the preview.

   Idempotent: it replaces whatever sits between the data-harness markers, so
   edit _harness.css / _harness.js and re-run:  node _inline.js
*/
const fs = require("fs");
const path = require("path");

const dir = __dirname;
const css = fs.readFileSync(path.join(dir, "_harness.css"), "utf8").trim();
const js = fs.readFileSync(path.join(dir, "_harness.js"), "utf8").trim();

const DEMOS = ["01-oscilloscope.html", "02-aperture.html", "03-manifest.html"];

const cssBlock = '<style data-harness>\n/* ===== inlined from _harness.css — edit that file, then run node _inline.js ===== */\n' + css + '\n</style>';
const jsBlock = '<script data-harness>\n/* ===== inlined from _harness.js — edit that file, then run node _inline.js ===== */\n' + js + '\n</' + 'script>';

for (const name of DEMOS) {
  const file = path.join(dir, name);
  let html = fs.readFileSync(file, "utf8");
  let didCss = false, didJs = false;

  if (/<style data-harness>[\s\S]*?<\/style>/.test(html)) {
    html = html.replace(/<style data-harness>[\s\S]*?<\/style>/, () => cssBlock);
    didCss = true;
  } else if (/<link rel="stylesheet" href="_harness\.css">/.test(html)) {
    html = html.replace(/<link rel="stylesheet" href="_harness\.css">/, () => cssBlock);
    didCss = true;
  }

  if (/<script data-harness>[\s\S]*?<\/script>/.test(html)) {
    html = html.replace(/<script data-harness>[\s\S]*?<\/script>/, () => jsBlock);
    didJs = true;
  } else if (/<script src="_harness\.js"><\/script>/.test(html)) {
    html = html.replace(/<script src="_harness\.js"><\/script>/, () => jsBlock);
    didJs = true;
  }

  if (!didCss || !didJs) {
    console.error("FAIL " + name + " — css:" + didCss + " js:" + didJs);
    process.exitCode = 1;
    continue;
  }

  fs.writeFileSync(file, html);
  console.log("ok   " + name + "  " + (html.length / 1024).toFixed(0) + " KB, standalone");
}
