import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { initializeTelegram } from './telegram'
import './styles.css'
import './files.css'
import './work.css'

initializeTelegram()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><App /></React.StrictMode>,
)
