import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom';
import {
  TrendingUp, Cpu, BatteryCharging, DollarSign, ShieldAlert,
  Settings as SettingsIcon, Users, Database, ArrowLeftRight, Radio, SearchCheck,
} from 'lucide-react';
import { useApp } from '../state/AppContext';
import type { UserRole } from '../api/client';

type NavItem = { to: string; label: string; icon: React.ReactNode };

const DISPATCHER_NAV: NavItem[] = [
  { to: '/dispatcher/asset', label: 'Asset Detail & SCADA', icon: <Database size={18} /> },
  { to: '/dispatcher/optimization', label: 'Optimization Schedule', icon: <TrendingUp size={18} /> },
  { to: '/dispatcher/forecast', label: 'Price Forecast', icon: <Cpu size={18} /> },
  { to: '/dispatcher/grid-risk', label: 'Стан енергосистеми', icon: <Radio size={18} /> },
];

const DIRECTOR_NAV: NavItem[] = [
  { to: '/director/executive', label: 'Executive Overview', icon: <TrendingUp size={18} /> },
  { to: '/director/roi', label: 'ROI & Payback', icon: <DollarSign size={18} /> },
  { to: '/director/accuracy', label: 'Точність прогнозу', icon: <Radio size={18} /> },
  { to: '/director/scenarios', label: 'Risk Scenarios', icon: <ShieldAlert size={18} /> },
];

const SHARED_NAV: NavItem[] = [
  { to: '/settings', label: 'Settings & Tariffs', icon: <SettingsIcon size={18} /> },
  { to: '/audit', label: 'Audit & Model Decisions', icon: <Users size={18} /> },
  { to: '/data-audit', label: 'Довідка про дані дня', icon: <SearchCheck size={18} /> },
];

const PAGE_TITLES: Record<string, string> = {
  '/dispatcher/asset': 'Asset Telemetry & Live SCADA',
  '/dispatcher/optimization': 'Optimal BESS Arbitrage Schedule',
  '/dispatcher/forecast': 'Neural Price Predictor (LightGBM/XGBoost)',
  '/dispatcher/grid-risk': 'Стан енергосистеми — реальний операційний сигнал',
  '/director/executive': 'Executive Financial Overview',
  '/director/roi': 'Project Lifecycle Economics & NPV',
  '/director/accuracy': 'Точність прогнозу цін (реальний WAPE/MAPE)',
  '/director/scenarios': 'Stochastic Risk & Stress Analysis',
  '/settings': 'System Tariffs & Limit Configurator',
  '/audit': 'Four-Eyes Audit Trail & Model Decisions',
  '/data-audit': 'Довідка: що реально використано в розрахунку за дату',
};

export default function AppShell({ workspace }: { workspace: 'dispatcher' | 'director' }) {
  const { activeRole, setActiveRole, addLog } = useApp();
  const location = useLocation();
  const navigate = useNavigate();

  const workspaceNav = workspace === 'dispatcher' ? DISPATCHER_NAV : DIRECTOR_NAV;
  const otherWorkspaceLabel = workspace === 'dispatcher' ? 'Director Dashboard' : 'Dispatcher Console';
  const otherWorkspaceHome = workspace === 'dispatcher' ? '/director/executive' : '/dispatcher/asset';

  const title = PAGE_TITLES[location.pathname] || (workspace === 'dispatcher' ? 'Dispatcher Console' : 'Director Dashboard');

  return (
    <div className="dashboard-container">
      <aside className="sidebar">
        <div className="logo-section">
          <BatteryCharging style={{ color: '#3b82f6' }} />
          <span className="logo-text">SmartBESS EMS</span>
        </div>

        <div
          className="nav-item"
          style={{ background: 'rgba(59,130,246,0.08)', marginBottom: '12px' }}
          onClick={() => navigate(otherWorkspaceHome)}
        >
          <ArrowLeftRight size={18} />
          <span>Перейти: {otherWorkspaceLabel}</span>
        </div>

        <div className="nav-section-title">{workspace === 'dispatcher' ? 'Dispatcher Console' : 'Director Dashboard'}</div>
        {workspaceNav.map((item) => (
          <Link key={item.to} to={item.to} style={{ textDecoration: 'none', color: 'inherit' }}>
            <div className={`nav-item ${location.pathname === item.to ? 'active' : ''}`}>
              {item.icon}
              <span>{item.label}</span>
            </div>
          </Link>
        ))}

        <div className="nav-section-title">Спільне</div>
        {SHARED_NAV.map((item) => (
          <Link key={item.to} to={item.to} style={{ textDecoration: 'none', color: 'inherit' }}>
            <div className={`nav-item ${location.pathname === item.to ? 'active' : ''}`}>
              {item.icon}
              <span>{item.label}</span>
            </div>
          </Link>
        ))}
      </aside>

      <main className="main-workspace">
        <header className="top-header">
          <div className="header-title-section">
            <h1 className="header-title">{title}</h1>
            <span className="status-badge online">
              <Database size={12} />
              SCADA Modbus: ONLINE (127.0.0.1:5020)
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Users size={16} style={{ color: '#9ca3af' }} />
              <select
                className="rbac-selector"
                value={activeRole}
                onChange={(e) => {
                  setActiveRole(e.target.value as UserRole);
                  addLog('AUTH', `Пользователь переключил сессию на роль: ${e.target.value}`, 'info');
                }}
              >
                <option value="Viewer">Viewer (Read-Only)</option>
                <option value="Operator">Operator (Grid Dispatch)</option>
                <option value="Manager">Manager (CFO/CLevel)</option>
                <option value="Admin">Admin (IT System)</option>
              </select>
            </div>
          </div>
        </header>

        <section className="page-content">
          <Outlet />
        </section>
      </main>
    </div>
  );
}
