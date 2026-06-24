import { Routes, Route } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import Dashboard from './pages/Dashboard';
import Sourcing from './pages/Sourcing';
import DescriptionGenerator from "./pages/DescriptionGenerator"


// Pages temporaires pour le test
const Inventory = () => <div className="p-10"><h2 className="text-2xl font-bold">📦 Inventaire Complet</h2></div>;
const Analytics = () => <div className="p-10"><h2 className="text-2xl font-bold">📈 Statistiques de vente</h2></div>;

function App() {
  return (
    <div className="flex h-screen w-full overflow-hidden">
      {/* Le menu latéral reste fixe à gauche */}
      <Sidebar />

      {/* Le contenu principal défile à droite */}
      <main className="flex-1 overflow-y-auto">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/inventory" element={<Inventory />} />
          <Route path="/sourcing" element={<Sourcing />} /> {/* Ajout de la route */}
          <Route path="/generateur" element={<DescriptionGenerator />} />
          <Route path="/inventory" element={<Inventory />} />
          <Route path="/analytics" element={<Analytics />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;