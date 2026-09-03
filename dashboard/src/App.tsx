import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import HealthBadge from "./components/HealthBadge";
import TransactionsPage from "./pages/TransactionsPage";
import PoliticiansPage from "./pages/PoliticiansPage";
import PoliticianProfilePage from "./pages/PoliticianProfilePage";
import FilingDetailPage from "./pages/FilingDetailPage";

export default function App() {
  return (
    <Layout>
      <HealthBadge />
      <Routes>
        <Route path="/" element={<Navigate to="/transactions" replace />} />
        <Route path="/transactions" element={<TransactionsPage />} />
        <Route path="/politicians" element={<PoliticiansPage />} />
        <Route path="/politicians/:politicianId" element={<PoliticianProfilePage />} />
        <Route path="/filings/:docId" element={<FilingDetailPage />} />
        <Route path="*" element={<Navigate to="/transactions" replace />} />
      </Routes>
    </Layout>
  );
}
