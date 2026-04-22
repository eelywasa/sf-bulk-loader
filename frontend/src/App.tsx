import { createBrowserRouter, createHashRouter, RouterProvider, Navigate } from 'react-router-dom'
import AppShell from './layout/AppShell'
import ProtectedRoute from './components/ProtectedRoute'
import Login from './pages/Login'
import ForgotPassword from './pages/ForgotPassword'
import ResetPassword from './pages/ResetPassword'
import Dashboard from './pages/Dashboard'
import Connections from './pages/Connections'
import PlansPage from './pages/PlansPage'
import PlanEditor from './pages/PlanEditor'
import RunsPage from './pages/RunsPage'
import RunDetail from './pages/RunDetail'
import JobDetail from './pages/JobDetail'
import FilesPage from './pages/FilesPage'
import Settings from './pages/Settings'
import Profile from './pages/Profile'
import VerifyEmail from './pages/VerifyEmail'
import SettingsEmailPage from './pages/SettingsEmailPage'
import SettingsSalesforcePage from './pages/SettingsSalesforcePage'
import SettingsPartitioningPage from './pages/SettingsPartitioningPage'
import SettingsSecurityPage from './pages/SettingsSecurityPage'
import ForbiddenPage from './pages/ForbiddenPage'
import AdminUsersPage from './pages/AdminUsersPage'

const createRouter = import.meta.env.VITE_ROUTER === 'hash' ? createHashRouter : createBrowserRouter

const router = createRouter([
  { path: '/login', element: <Login /> },
  { path: '/forgot-password', element: <ForgotPassword /> },
  { path: '/reset-password/:token', element: <ResetPassword /> },
  { path: '/verify-email/:token', element: <VerifyEmail /> },
  // /403 is accessible even when authenticated (no ProtectedRoute wrapper)
  { path: '/403', element: <ForbiddenPage /> },
  {
    element: (
      <ProtectedRoute>
        <AppShell />
      </ProtectedRoute>
    ),
    children: [
      { path: '/', element: <Dashboard /> },
      {
        path: '/connections',
        element: (
          <ProtectedRoute permission="connections.view">
            <Connections />
          </ProtectedRoute>
        ),
      },
      {
        path: '/plans',
        element: (
          <ProtectedRoute permission="plans.view">
            <PlansPage />
          </ProtectedRoute>
        ),
      },
      {
        path: '/plans/:id',
        element: (
          <ProtectedRoute permission="plans.view">
            <PlanEditor />
          </ProtectedRoute>
        ),
      },
      {
        path: '/runs',
        element: (
          <ProtectedRoute permission="runs.view">
            <RunsPage />
          </ProtectedRoute>
        ),
      },
      {
        path: '/runs/:id',
        element: (
          <ProtectedRoute permission="runs.view">
            <RunDetail />
          </ProtectedRoute>
        ),
      },
      {
        path: '/runs/:runId/jobs/:jobId',
        element: (
          <ProtectedRoute permission="runs.view">
            <JobDetail />
          </ProtectedRoute>
        ),
      },
      {
        path: '/files',
        element: (
          <ProtectedRoute permission="files.view">
            <FilesPage />
          </ProtectedRoute>
        ),
      },
      // Legacy /settings → tabs for Notifications; kept for backward compat
      {
        path: '/settings',
        element: (
          <ProtectedRoute permission="system.settings">
            <Settings />
          </ProtectedRoute>
        ),
      },
      // Admin settings pages (DB-backed, SFBL-157)
      {
        path: '/settings/email',
        element: (
          <ProtectedRoute permission="system.settings">
            <SettingsEmailPage />
          </ProtectedRoute>
        ),
      },
      {
        path: '/settings/salesforce',
        element: (
          <ProtectedRoute permission="system.settings">
            <SettingsSalesforcePage />
          </ProtectedRoute>
        ),
      },
      {
        path: '/settings/partitioning',
        element: (
          <ProtectedRoute permission="system.settings">
            <SettingsPartitioningPage />
          </ProtectedRoute>
        ),
      },
      {
        path: '/settings/security',
        element: (
          <ProtectedRoute permission="system.settings">
            <SettingsSecurityPage />
          </ProtectedRoute>
        ),
      },
      { path: '/profile', element: <Profile /> },
      {
        path: '/admin/users',
        element: (
          <ProtectedRoute permission="users.manage">
            <AdminUsersPage />
          </ProtectedRoute>
        ),
      },
    ],
  },
  // Catch-all redirect
  { path: '*', element: <Navigate to="/" replace /> },
])

export default function App() {
  return <RouterProvider router={router} />
}
