import { Routes, Route } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import Dashboard from './pages/Dashboard';
import Liquidation from './pages/Liquidation';
import Sourcing from './pages/Sourcing';
import PreparerAnnonces from './pages/PreparerAnnonces';
import DescriptionGenerator from "./pages/DescriptionGenerator"
import TaskHistory from './pages/TaskHistory';
import Journal from './pages/Journal';
import Maintenance from './pages/Maintenance';
import Achats from './pages/Achats';
import Ventes from './pages/Ventes';


// Pages temporaires pour le test
const Inventory = () => <div className="p-10"><h2 className="text-2xl font-bold">📦 Inventaire Complet</h2></div>;

function App() {
  return (
    <div className="flex h-screen w-full overflow-hidden">
      {/* Le menu latéral reste fixe à gauche */}
      <Sidebar />

      {/* Le contenu principal défile à droite */}
      <main className="flex-1 overflow-y-auto">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/liquidation" element={<Liquidation />} />
          <Route path="/inventory" element={<Inventory />} />
          <Route path="/sourcing" element={<Sourcing />} /> {/* Ajout de la route */}
          <Route path="/preparer-annonces" element={<PreparerAnnonces />} />
          <Route path="/generateur" element={<DescriptionGenerator />} />
          <Route path="/achats" element={<Achats />} />
          <Route path="/ventes" element={<Ventes />} />
          <Route path="/historique" element={<TaskHistory />} />
          <Route path="/journal" element={<Journal />} />
          <Route path="/maintenance" element={<Maintenance />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;