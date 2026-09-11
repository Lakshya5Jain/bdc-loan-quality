import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import App from './App'
import Screener from './pages/Screener'
import { Book, Results } from './pages/Strategy'
import Overview from './pages/Overview'
import Data from './pages/Data'
import BdcList from './pages/BdcList'
import BdcDetail from './pages/BdcDetail'
import LoanDetail from './pages/LoanDetail'
import Borrowers from './pages/Borrowers'
import BorrowerDetail from './pages/BorrowerDetail'
import Status from './pages/Status'
import Insights from './pages/Insights'
import Validation from './pages/Validation'
import Vintages from './pages/Vintages'
import Scorecards from './pages/Scorecards'
import Glossary from './pages/Glossary'
import Method from './pages/Method'
import StaleMarks from './pages/StaleMarks'
import Neighbors from './pages/Neighbors'
import NotFound from './pages/NotFound'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          <Route index element={<Overview />} />
          <Route path="data" element={<Data />} />
          <Route path="methods" element={<Method />} />
          <Route path="results" element={<Results />} />
          <Route path="book" element={<Book />} />
          <Route path="screener" element={<Screener />} />
          <Route path="bdcs" element={<BdcList />} />
          <Route path="bdcs/:cik" element={<BdcDetail />} />
          <Route path="loans/:loanId" element={<LoanDetail />} />
          <Route path="borrowers" element={<Borrowers />} />
          <Route path="borrowers/:key" element={<BorrowerDetail />} />
          <Route path="status" element={<Status />} />
          <Route path="insights" element={<Insights />} />
          <Route path="validation" element={<Validation />} />
          <Route path="vintages" element={<Vintages />} />
          <Route path="sectors" element={<Vintages />} />
          <Route path="scorecards" element={<Scorecards />} />
          <Route path="glossary" element={<Glossary />} />
          <Route path="stale-marks" element={<StaleMarks />} />
          <Route path="neighbors" element={<Neighbors />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
