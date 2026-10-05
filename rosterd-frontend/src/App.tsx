import { Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from './components/AppShell';
import { Ask } from './screens/Ask';
import { Contracts } from './screens/Contracts';
import { Ingest } from './screens/Ingest';
import { Landing } from './screens/Landing';
import { Monitor } from './screens/Monitor';
import { Review } from './screens/Review';
import { Roster } from './screens/Roster';
import { RunDetail } from './screens/RunDetail';

export function App() {
  return (
    <Routes>
      {/* The one screen that renders outside <AppShell> — no nav bar, no
          live-data chrome. Its only job is the pitch and a CTA into /ingest. */}
      <Route path="/" element={<Landing />} />
      <Route element={<AppShell />}>
        <Route path="/ingest" element={<Ingest />} />
        <Route path="/review" element={<Review />} />
        <Route path="/ask" element={<Ask />} />
        <Route path="/roster" element={<Roster />} />
        {/* Screen 5 is a detail view, reached from Ask, Roster, or an event. */}
        <Route path="/runs/:runId" element={<RunDetail />} />
        <Route path="/contracts" element={<Contracts />} />
        <Route path="/monitor" element={<Monitor />} />
        <Route path="*" element={<Navigate to="/ingest" replace />} />
      </Route>
    </Routes>
  );
}
