import { StrictMode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import '@fontsource/assistant/hebrew-400.css';
import '@fontsource/assistant/hebrew-500.css';
import '@fontsource/assistant/hebrew-600.css';
import '@fontsource/assistant/hebrew-700.css';
import '@fontsource/assistant/latin-400.css';
import '@fontsource/assistant/latin-500.css';
import '@fontsource/assistant/latin-600.css';
import '@fontsource/assistant/latin-700.css';
import '@fontsource/rubik/hebrew-500.css';
import '@fontsource/rubik/hebrew-600.css';
import '@fontsource/rubik/hebrew-700.css';
import '@fontsource/rubik/latin-500.css';
import '@fontsource/rubik/latin-600.css';
import '@fontsource/rubik/latin-700.css';
import './styles/theme.css';
import App from './App';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      gcTime: 60 * 60 * 1000,
    },
  },
});

const rootEl = document.getElementById('root');
if (!rootEl) {
  throw new Error('#root element missing in index.html');
}

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
