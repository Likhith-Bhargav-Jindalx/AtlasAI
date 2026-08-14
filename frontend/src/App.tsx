import { Route, Routes, useParams } from 'react-router-dom'
import { LoginPage } from './pages/LoginPage'
import { InboxPage } from './pages/agent/InboxPage'
import { LiveRecordingPage } from './pages/agent/LiveRecordingPage'
import { ReviewPage } from './pages/agent/ReviewPage'
import { AgentLibraryPage } from './pages/agent/AgentLibraryPage'
import { LibraryPage } from './pages/dev/LibraryPage'
import { AuthoringPage } from './pages/dev/AuthoringPage'
import { RequestQueuePage } from './pages/dev/RequestQueuePage'
import { CostDashboardPage } from './pages/dev/CostDashboardPage'
import { ApprovalsPage } from './pages/ApprovalsPage'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { AppLayout } from './layout/AppLayout'
import { RoleProvider } from './layout/RoleContext'

// AuthoringPage keeps the in-flight authoring run id in local state, and the
// router reuses one instance across client-side navigations between sibling
// recordings. Keying on the param forces a remount so recording A's run can
// never show up under recording B.
function KeyedAuthoringPage() {
  const { recordingId } = useParams<{ recordingId: string }>()
  return <AuthoringPage key={recordingId} />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route
          element={
            <RoleProvider>
              <AppLayout />
            </RoleProvider>
          }
        >
          <Route path="/" element={<div className="p-8">Pick a screen from the sidebar.</div>} />
          <Route path="/agent" element={<InboxPage />} />
          <Route path="/agent/record" element={<LiveRecordingPage />} />
          <Route path="/agent/library" element={<AgentLibraryPage />} />
          <Route path="/agent/recordings/:recordingId" element={<ReviewPage />} />
          <Route path="/dev" element={<LibraryPage />} />
          <Route path="/dev/requests" element={<RequestQueuePage />} />
          <Route path="/dev/costs" element={<CostDashboardPage />} />
          <Route path="/dev/recordings/:recordingId" element={<KeyedAuthoringPage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
        </Route>
      </Route>
    </Routes>
  )
}
