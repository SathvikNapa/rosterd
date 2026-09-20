import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MotionConfig } from 'motion/react';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App';
import { LiveProvider } from './lib/live/LiveProvider';
import { SessionProvider } from './lib/session';
import './styles/global.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* reducedMotion="user" defers to the OS setting: Motion then animates
        opacity only and skips transforms/layout, so no component here has to
        branch on prefers-reduced-motion itself. */}
    <MotionConfig reducedMotion="user">
      <BrowserRouter>
        <SessionProvider>
          <LiveProvider>
            <App />
          </LiveProvider>
        </SessionProvider>
      </BrowserRouter>
    </MotionConfig>
  </StrictMode>,
);
