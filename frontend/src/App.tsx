import { useEffect, useState } from 'react'
import { Generate } from './pages/Generate'
import { Home } from './pages/Home'

/**
 * Minimal history-based routing: the whole product is two screens, so a
 * router dependency would cost more than it explains. The job id lives in
 * the URL so a generation page can be reloaded or shared.
 */
export default function App() {
  const [jobId, setJobId] = useState<string | null>(
    () => new URLSearchParams(window.location.search).get('job'))

  useEffect(() => {
    const onPop = () => {
      setJobId(new URLSearchParams(window.location.search).get('job'))
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  function start(id: string) {
    window.history.pushState({}, '', `?job=${encodeURIComponent(id)}`)
    setJobId(id)
  }

  function reset() {
    window.history.pushState({}, '', window.location.pathname)
    setJobId(null)
  }

  return (
    <div className="app">
      <header className="masthead">
        <button className="brand" onClick={reset}
                style={{ background: 'none', border: 0, color: 'inherit', padding: 0 }}>
          <span className="brand-mark" />
          <span>Brick Foundry</span>
        </button>
        <a className="masthead-link" href="https://library.ldraw.org/"
           target="_blank" rel="noreferrer">Powered by LDraw</a>
      </header>

      {jobId
        ? <Generate jobId={jobId} onStartOver={reset} />
        : <Home onStarted={start} />}
    </div>
  )
}
