import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import App from './App'
import Screener from './pages/Screener'
import BdcList from './pages/BdcList'
import BdcDetail from './pages/BdcDetail'
import LoanDetail from './pages/LoanDetail'
import Borrowers from './pages/Borrowers'
import BorrowerDetail from './pages/BorrowerDetail'
import Status from './pages/Status'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          <Route index element={<Screener />} />
          <Route path="bdcs" element={<BdcList />} />
          <Route path="bdcs/:cik" element={<BdcDetail />} />
          <Route path="loans/:loanId" element={<LoanDetail />} />
          <Route path="borrowers" element={<Borrowers />} />
          <Route path="borrowers/:key" element={<BorrowerDetail />} />
          <Route path="status" element={<Status />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
