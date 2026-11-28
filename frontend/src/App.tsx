import { BrowserRouter, Route, Routes } from 'react-router-dom'

import { CustomerShell } from './components/CustomerShell'
import { ReviewerShell } from './components/ReviewerShell'
import { CasePage } from './pages/CasePage'
import { EscalationDetailPage } from './pages/EscalationDetailPage'
import { EscalationsPage } from './pages/EscalationsPage'
import { CasesPage } from './pages/CasesPage'
import { CustomerHomeStub } from './pages/CustomerHomeStub'
import { LoginPage } from './pages/LoginPage'
import { NewCasePage } from './pages/NewCasePage'
import { OrdersPage } from './pages/OrdersPage'
import { ProposalDetailPage } from './pages/ProposalDetailPage'
import { ReviewOverviewPage } from './pages/ReviewOverviewPage'
import { ReviewQueuePage } from './pages/ReviewQueuePage'
import { GuestOnly } from './routes/GuestOnly'
import { RequireCustomer } from './routes/RequireCustomer'
import { RequireReviewer } from './routes/RequireReviewer'
import { SessionRedirect } from './routes/SessionRedirect'

/**
 * Phase 10: customer P2 + reviewer P3 (queue, detail, overview).
 * AuthProvider stays in main.tsx outside this router.
 */
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<GuestOnly />}>
          <Route path="/login" element={<LoginPage />} />
        </Route>

        <Route element={<RequireCustomer />}>
          <Route element={<CustomerShell />}>
            <Route path="/" element={<CustomerHomeStub />} />
            <Route path="/orders" element={<OrdersPage />} />
            <Route path="/cases" element={<CasesPage />} />
            <Route path="/cases/new" element={<NewCasePage />} />
            <Route path="/cases/:ticketId" element={<CasePage />} />
          </Route>
        </Route>

        <Route element={<RequireReviewer />}>
          <Route element={<ReviewerShell />}>
            <Route path="/review" element={<ReviewOverviewPage />} />
            <Route path="/review/queue" element={<ReviewQueuePage />} />
            <Route path="/review/proposals/:proposalId" element={<ProposalDetailPage />} />
            <Route path="/review/escalations" element={<EscalationsPage />} />
            <Route path="/review/escalations/:ticketId" element={<EscalationDetailPage />} />
          </Route>
        </Route>

        <Route path="*" element={<SessionRedirect />} />
      </Routes>
    </BrowserRouter>
  )
}
