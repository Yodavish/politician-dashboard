import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import TransactionsPage from "./pages/TransactionsPage";
import PoliticiansPage from "./pages/PoliticiansPage";
import PoliticianProfilePage from "./pages/PoliticianProfilePage";
import FilingDetailPage from "./pages/FilingDetailPage";
import SignalsPage from "./pages/SignalsPage";
import SignalDetailPage from "./pages/SignalDetailPage";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/transactions" replace />} />
        <Route path="/transactions" element={<TransactionsPage />} />
        <Route path="/politicians" element={<PoliticiansPage />} />
        <Route path="/politicians/:politicianId" element={<PoliticianProfilePage />} />
        <Route path="/filings/:docId" element={<FilingDetailPage />} />
        <Route path="/signals" element={<SignalsPage />} />
        <Route path="/signals/:signalId" element={<SignalDetailPage />} />
        <Route path="*" element={<Navigate to="/transactions" replace />} />
      </Routes>
    </Layout>
  );
}
