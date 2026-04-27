import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

// NOTE: the lightweight-charts "Object is disposed" filter lives in
// index.html as a classic inline <script> so it registers BEFORE the
// dev runtime-error overlay's deferred module script. See v7.1.7 notes.

createRoot(document.getElementById("root")!).render(<App />);
