import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import AppShell from './layout/AppShell'
import Dashboard from './pages/Dashboard'
import Connections from './pages/Connections'
import PlansPage from './pages/PlansPage'
import PlanEditor from './pages/PlanEditor'
import RunsPage from './pages/RunsPage'
import RunDetail from './pages/RunDetail'
import JobDetail from './pages/JobDetail'
import FilesPage from './pages/FilesPage'

const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { path: '/', element: <Dashboard /> },
      { path: '/connections', element: <Connections /> },
      { path: '/plans', element: <PlansPage /> },
      { path: '/plans/:id', element: <PlanEditor /> },
      { path: '/runs', element: <RunsPage /> },
      { path: '/runs/:id', element: <RunDetail /> },
      { path: '/runs/:runId/jobs/:jobId', element: <JobDetail /> },
      { path: '/files', element: <FilesPage /> },
    ],
  },
])

export default function App() {
  return <RouterProvider router={router} />
}
