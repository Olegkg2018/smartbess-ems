import { lazy, useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AppProvider, useApp } from './state/AppContext';
import AppShell from './layouts/AppShell';
import ApprovalModal from './components/ApprovalModal';
import Login from './pages/shared/Login';
import { fetchAuthConfig, getStoredAccessLevel } from './api/client';

const AssetDetail = lazy(() => import('./pages/dispatcher/AssetDetail'));
const OptimizationSchedule = lazy(() => import('./pages/dispatcher/OptimizationSchedule'));
const PriceForecast = lazy(() => import('./pages/dispatcher/PriceForecast'));
const GridRiskPanel = lazy(() => import('./pages/dispatcher/GridRiskPanel'));

const ExecutiveOverview = lazy(() => import('./pages/director/ExecutiveOverview'));
const RoiPayback = lazy(() => import('./pages/director/RoiPayback'));
const ForecastAccuracy = lazy(() => import('./pages/director/ForecastAccuracy'));
const RiskScenarios = lazy(() => import('./pages/director/RiskScenarios'));

const Settings = lazy(() => import('./pages/shared/Settings'));
const Audit = lazy(() => import('./pages/shared/Audit'));
const DataAudit = lazy(() => import('./pages/shared/DataAudit'));
const About = lazy(() => import('./pages/shared/About'));

// 2026-09-28 (ревью продуктивності): сторінки вантажаться ліниво — раніше
// всі 12 (разом із recharts) йшли одним ~800 КБ бандлом навіть на сторінку,
// що графіків не має. Suspense — в AppShell навколо <Outlet/>.
function DefaultRedirect() {
  const { activeRole } = useApp();
  const home = activeRole === 'Manager' || activeRole === 'Viewer' ? '/director/executive' : '/dispatcher/asset';
  return <Navigate to={home} replace />;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<DefaultRedirect />} />

      <Route element={<AppShell workspace="dispatcher" />}>
        <Route path="/dispatcher/asset" element={<AssetDetail />} />
        <Route path="/dispatcher/optimization" element={<OptimizationSchedule />} />
        <Route path="/dispatcher/forecast" element={<PriceForecast />} />
        <Route path="/dispatcher/grid-risk" element={<GridRiskPanel />} />
      </Route>

      <Route element={<AppShell workspace="director" />}>
        <Route path="/director/executive" element={<ExecutiveOverview />} />
        <Route path="/director/roi" element={<RoiPayback />} />
        <Route path="/director/accuracy" element={<ForecastAccuracy />} />
        <Route path="/director/scenarios" element={<RiskScenarios />} />
      </Route>

      <Route element={<AppShell workspace="dispatcher" />}>
        <Route path="/settings" element={<Settings />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="/data-audit" element={<DataAudit />} />
        <Route path="/about" element={<About />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

// Екран входу: якщо сервер вимагає пароль (ACCESS_PASSWORD_FULL) і сесія ще
// не авторизована — показуємо форму. AppProvider монтується лише після входу,
// щоб жодного API-запиту не було без пароля.
function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<'loading' | 'login' | 'ready'>('loading');
  useEffect(() => {
    if (getStoredAccessLevel()) { setState('ready'); return; }
    fetchAuthConfig()
      .then((c) => setState(c.password_required ? 'login' : 'ready'))
      .catch(() => setState('ready'));
  }, []);
  if (state === 'loading') return null;
  if (state === 'login') return <Login onSuccess={() => setState('ready')} />;
  return <>{children}</>;
}
export default function App() {
  return (
    <BrowserRouter>
      <AuthGate>
        <AppProvider>
          <AppRoutes />
          <ApprovalModal />
        </AppProvider>
      </AuthGate>
    </BrowserRouter>
  );
}
