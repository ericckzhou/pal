import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import ErrorBoundary from "./ErrorBoundary";
import "./styles.css";

const start = () =>
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </StrictMode>,
  );

// The public demo (vite --mode demo) answers /api calls in the browser. MODE is fixed at build time,
// so a normal build drops this branch and ships none of the demo code.
if (import.meta.env.MODE === "demo") import("./demo/install").then((d) => { d.install(); start(); });
else start();
