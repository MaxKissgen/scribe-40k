import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./sheet/sheet.css";
import "./sheet/print.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
