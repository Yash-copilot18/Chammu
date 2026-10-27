import React from 'react'
import Sidebar from './components/Sidebar/Sidebar'
import Header from './components/Header/Header'
import AppRoutes from './routes/AppRoutes'
import { ToastContainer, Modal } from './components/Common'
import { AppProvider } from './utils/AppContext'
import UnconfiguredBanner from './components/Site/UnconfiguredBanner'

export default function App() {
  return (
    <AppProvider>
      <div className="flex h-screen overflow-hidden bg-ge-navy">
        <Sidebar />
        <div className="flex flex-col flex-1 overflow-hidden">
          <Header />
          <main className="flex-1 overflow-y-auto bg-ge-navy p-4">
            <UnconfiguredBanner />
            <AppRoutes />
          </main>
        </div>
      </div>
      <ToastContainer />
      <Modal />
    </AppProvider>
  )
}