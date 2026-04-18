import { createBrowserRouter, createHashRouter, RouterProvider } from 'react-router-dom'
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

const createRouter = import.meta.env.VITE_ROUTER === 'hash' ? createHashRouter : createBrowserRouter

const router = createRouter([
  { path: '/login', element: <Login /> },
  { path: '/forgot-password', element: <ForgotPassword /> },
  { path: '/reset-password/:token', element: <ResetPassword /> },
  { path: '/verify-email/:token', element: <VerifyEmail /> },
  {
    element: (
      <ProtectedRoute>
        <AppShell />
      </ProtectedRoute>
    ),
    children: [
      { path: '/', element: <Dashboard /> },
      { path: '/connections', element: <Connections /> },
      { path: '/plans', element: <PlansPage /> },
      { path: '/plans/:id', element: <PlanEditor /> },
      { path: '/runs', element: <RunsPage /> },
      { path: '/runs/:id', element: <RunDetail /> },
      { path: '/runs/:runId/jobs/:jobId', element: <JobDetail /> },
      { path: '/files', element: <FilesPage /> },
      { path: '/settings', element: <Settings /> },
      { path: '/profile', element: <Profile /> },
    ],
  },
])

export default function App() {
  return <RouterProvider router={router} />
}
