import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AppProvider, useApp } from './state/AppContext';
import AppShell from './layouts/AppShell';
import ApprovalModal from './components/ApprovalModal';

import AssetDetail from './pages/dispatcher/AssetDetail';
import OptimizationSchedule from './pages/dispatcher/OptimizationSchedule';
import PriceForecast from './pages/dispatcher/PriceForecast';
import GridRiskPanel from './pages/dispatcher/GridRiskPanel';

import ExecutiveOverview from './pages/director/ExecutiveOverview';
import RoiPayback from './pages/director/RoiPayback';
import ForecastAccuracy from './pages/director/ForecastAccuracy';
import RiskScenarios from './pages/director/RiskScenarios';

import Settings from './pages/shared/Settings';
import Audit from './pages/shared/Audit';
import DataAudit from './pages/shared/DataAudit';

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
