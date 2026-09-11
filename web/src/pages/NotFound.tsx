import { Link } from 'react-router-dom'
import { PageHeader } from '../components/Page'

export default function NotFound() {
  return (
    <div>
      <PageHeader eyebrow="Not found" title="That page does not exist" lede="The address may be mistyped, or the BDC, loan or borrower it points to is not in the data." />
      <p>Start from the <Link to="/">overview</Link>, the <Link to="/bdcs">BDC list</Link>, or the <Link to="/borrowers">borrower search</Link>.</p>
    </div>
  )
}
