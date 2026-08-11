import React from "react";
import { createRoot } from "react-dom/client";

import SerialConsolePage from "./pages/SerialConsolePage";
import "./styles/console.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <SerialConsolePage />
  </React.StrictMode>,
);
