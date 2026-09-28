import { lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AppProvider, useApp } from './state/AppContext';
import AppShell from './layouts/AppShell';
import ApprovalModal from './components/ApprovalModal';

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

export default function App() {
  return (
    <BrowserRouter>
      <AppProvider>
        <AppRoutes />
        <ApprovalModal />
      </AppProvider>
    </BrowserRouter>
  );
}
