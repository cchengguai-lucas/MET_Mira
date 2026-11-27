import { useParams } from 'react-router-dom'
import { IssueView } from '../components/IssueDetail'

export function IssuePage() {
  const { key } = useParams()
  if (!key) return null
  return (
    <div className="page" style={{ padding: 0 }}>
      <IssueView issueKey={key.toUpperCase()} />
    </div>
  )
}
