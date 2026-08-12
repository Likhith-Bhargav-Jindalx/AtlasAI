import { Route, Routes } from 'react-router-dom'
import { LoginPage } from './pages/LoginPage'
import { InboxPage } from './pages/agent/InboxPage'
import { LiveRecordingPage } from './pages/agent/LiveRecordingPage'
import { ReviewPage } from './pages/agent/ReviewPage'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { AppLayout } from './layout/AppLayout'
import { RoleProvider } from './layout/RoleContext'

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
          <Route path="/agent/recordings/:recordingId" element={<ReviewPage />} />
          <Route path="/dev" element={<div className="p-8">Library — Task 9</div>} />
          <Route path="/approvals" element={<div className="p-8">Approvals — Task 11</div>} />
        </Route>
      </Route>
    </Routes>
  )
}
