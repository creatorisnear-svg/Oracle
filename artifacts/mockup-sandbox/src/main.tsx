import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

// v7.1.7: lightweight-charts has a known race where its internal
// ResizeObserver fires once after chart.remove() and throws "Object is
// disposed". The chart is already gone, so the error is harmless — but
// the dev runtime-error overlay surfaces it as a crash. Filter just
// THIS specific noise so real errors still surface normally.
function isLwcDisposalNoise(msg: unknown): boolean {
  if (typeof msg !== "string") return false;
  return msg.includes("Object is disposed");
}
window.addEventListener("error", (e) => {
  if (isLwcDisposalNoise(e.message) || isLwcDisposalNoise((e.error as Error)?.message)) {
    e.preventDefault();
    e.stopImmediatePropagation();
  }
});
window.addEventListener("unhandledrejection", (e) => {
  const reason = e.reason as Error | string | undefined;
  const msg = typeof reason === "string" ? reason : reason?.message;
  if (isLwcDisposalNoise(msg)) {
    e.preventDefault();
    e.stopImmediatePropagation();
  }
});

createRoot(document.getElementById("root")!).render(<App />);
