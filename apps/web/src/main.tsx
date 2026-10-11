import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";
import "./polish.css";
import { redirectLegacyPredict } from "./steam/routes";
import { consumeAuthMarker } from "./steam/sessionHint";

redirectLegacyPredict();
consumeAuthMarker();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
