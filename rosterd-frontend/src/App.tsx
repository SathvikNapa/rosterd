import { Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from './components/AppShell';
import { Ask } from './screens/Ask';
import { Contracts } from './screens/Contracts';
import { Federation } from './screens/Federation';
import { Ingest } from './screens/Ingest';
import { Monitor } from './screens/Monitor';
import { Review } from './screens/Review';
import { Roster } from './screens/Roster';
import { RunDetail } from './screens/RunDetail';

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/ingest" replace />} />
        <Route path="/ingest" element={<Ingest />} />
        <Route path="/review" element={<Review />} />
        <Route path="/ask" element={<Ask />} />
        <Route path="/roster" element={<Roster />} />
        {/* Screen 5 is a detail view, reached from Ask, Roster, or an event. */}
        <Route path="/runs/:runId" element={<RunDetail />} />
        <Route path="/contracts" element={<Contracts />} />
        <Route path="/federation" element={<Federation />} />
        <Route path="/monitor" element={<Monitor />} />
        <Route path="*" element={<Navigate to="/ingest" replace />} />
      </Route>
    </Routes>
  );
}
