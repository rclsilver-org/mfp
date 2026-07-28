import React from "react";
import { createRoot } from "react-dom/client";
import { initAuth } from "./keycloak";
import App from "./App";
import "./styles.css";

initAuth().then(() => {
  createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
});
