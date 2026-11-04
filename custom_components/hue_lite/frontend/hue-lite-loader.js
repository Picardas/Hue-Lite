// Loads Hue-Lite's dashboard script (hue-lite.js) on every page while Hue-Lite
// is set up. Home Assistant loads this file, not the script itself.
//
// A page never tries again for a script that didn't arrive (a phone whose
// network drops as the app opens, say): the Lights dashboard would show
// "Timeout waiting for strategy element" until the app was restarted. So this
// keeps asking for it, and the script, once it's here, has a dashboard showing
// that load again.

const STRATEGY_TAG = "ll-strategy-dashboard-hue-lite";
// Waits between tries, in seconds: about five minutes in all.
const WAITS = [1, 2, 4, 8, 15, 30, 30, 30, 30, 30, 30, 30, 30];

// hue-lite.js beside this file, with this file's version.
const script = new URL("hue-lite.js", import.meta.url);
script.search = new URL(import.meta.url).search;

let tries = 0;
let loading = false;
let waiting = null;

async function load() {
  clearTimeout(waiting);
  waiting = null;
  if (loading || customElements.get(STRATEGY_TAG)) return;
  loading = true;
  // A page remembers a script that failed and won't ask for it again, so
  // each try after the first asks for it at its own address.
  const url = new URL(script);
  if (tries) url.searchParams.set("try", tries);
  tries += 1;
  try {
    await import(url.href);
  } catch (err) {
    console.warn(`Hue-Lite: couldn't load its dashboard script (try ${tries})`, err);
    if (tries <= WAITS.length) waiting = setTimeout(load, WAITS[tries - 1] * 1000);
  } finally {
    loading = false;
  }
}

// Back online, or the app's opened again: try now rather than wait (even
// after the tries above have run out).
function tryNow() {
  if (document.visibilityState !== "hidden" && tries) load();
}

window.addEventListener("online", tryNow);
document.addEventListener("visibilitychange", tryNow);
load();
