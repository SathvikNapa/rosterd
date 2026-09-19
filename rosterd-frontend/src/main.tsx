import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App';
import { LiveProvider } from './lib/live/LiveProvider';
import { SessionProvider } from './lib/session';
import './styles/global.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <SessionProvider>
        <LiveProvider>
          <App />
        </LiveProvider>
      </SessionProvider>
    </BrowserRouter>
  </StrictMode>,
);
