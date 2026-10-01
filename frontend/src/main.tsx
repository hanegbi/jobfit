// Self-hosted, so the app still renders its own type with no network.
import "@fontsource-variable/geist";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles.css";

// The dataset changes only when a scrape runs, so a result stays good for a
// long time. Five minutes of staleTime means going back to a search you ran a
// moment ago is instant and silent, and gcTime keeps it in memory past that.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60_000,
      gcTime: 30 * 60_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
