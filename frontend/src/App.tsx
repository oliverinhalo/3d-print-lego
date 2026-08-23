import { useEffect, useState } from 'react'
import { Browse } from './pages/Browse'
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
  const [browsing, setBrowsing] = useState(
    () => new URLSearchParams(window.location.search).has('browse'))
  const [preset, setPreset] = useState('')

  useEffect(() => {
    const onPop = () => {
      const params = new URLSearchParams(window.location.search)
      setJobId(params.get('job'))
      setBrowsing(params.has('browse'))
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
    setBrowsing(false)
  }

  function openBrowse() {
    window.history.pushState({}, '', '?browse=1')
    setBrowsing(true)
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
        : browsing
          ? <Browse
              onClose={reset}
              onPick={(set) => {
                setPreset(set.set_num)
                setBrowsing(false)
                window.history.pushState({}, '', window.location.pathname)
              }} />
          : <Home onStarted={start} onBrowse={openBrowse} preset={preset} />}
    </div>
  )
}
