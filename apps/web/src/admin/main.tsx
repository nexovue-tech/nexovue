import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { AdminApp } from "./AdminApp";

import "../index.css";

const container = document.getElementById("root");
if (!container) throw new Error("admin.html is missing #root");

const client = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 15_000 } },
});

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <AdminApp />
    </QueryClientProvider>
  </StrictMode>,
);
