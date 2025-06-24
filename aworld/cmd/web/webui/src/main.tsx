import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './global.less'
import App from './pages/App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
