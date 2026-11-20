import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
// Phone layer (bottom tab bar, safe areas, touch targets): must load after App.css
import './responsive.css'
import { installTableLabels } from './services/tableLabels'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// Phone cards for list tables: label each value with its column name.
installTableLabels()
