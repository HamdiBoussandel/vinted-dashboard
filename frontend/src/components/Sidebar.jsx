import { Link, useLocation } from 'react-router-dom';
import { Search } from 'lucide-react';


// Import des icônes spécifiques
import { 
  LayoutDashboard, 
  Package, 
  Sparkles,
  BarChart3, 
  Settings 
} from 'lucide-react';

export default function Sidebar() {
  const location = useLocation();

  const menuItems = [
    { name: 'Tableau de bord', path: '/', icon: LayoutDashboard },
    { name: 'Mon Inventaire', path: '/inventory', icon: Package },
    { name: 'Sourcing', path: '/sourcing', icon: Search },
    { name: 'Generateur', path: '/generateur', icon: Sparkles }, // Nouvelle entrée
    { name: 'Erreurs', path: '/Errors', icon: BarChart3 },
    { name: 'Paramètres', path: '/settings', icon: Settings },
  ];

  const isActive = (path) => location.pathname === path;

  return (
    <aside className="w-64 h-full rounded-lg shrink-0 shadow-lg">
      {/* Header du Menu */}
      <div className="w-full flex flex-col px-6 py-6
        rounded-lg
        overflow-hidden">
        <h1 className="text-xl font-black tracking-tighter">
          VINTED<span className="text-cyan-400">PRO</span>
        </h1>
        <p className="text-[10px] text-slate-400 font-bold uppercase tracking-widest mt-1">
          Gestionnaire de Stock
        </p>
      </div>

      {/* Liens de navigation */}
      <nav className="flex-1 space-y-3 px-6 py-4 overflow-y-auto">
        {menuItems.map((item) => {
          const Icon = item.icon; // On définit le composant icône
          
          return (
            <Link
              key={item.path}
              to={item.path}
              className={`flex items-center gap-4 px-4 py-3 rounded-lg font-semibold transition-all duration-200 ${
                isActive(item.path)
                  ? 'bg-[#6ED8EF] text-white shadow-lg shadow-cyan-500/20 scale-[1.02]'
                  : 'text-slate-500 hover:bg-slate-100 hover:text-cyan-600'
              }`}
            >
              <Icon 
                size={20} 
                strokeWidth={isActive(item.path) ? 2.5 : 2} 
                className={isActive(item.path) ? 'text-white' : 'text-slate-400'}
              />
              <span className="text-sm">{item.name}</span>
            </Link>
          );
        })}
      </nav>

      {/* Footer du Menu (Statut API) */}
      <div className="p-6 border-t border-slate-100">
        <div className="flex items-center gap-3 bg-slate-50 p-3 rounded-lg border border-slate-100">
          <div className="h-2 w-2 bg-green-500 rounded-lg animate-pulse"></div>
          <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-tighter">
            Système Opérationnel
          </span>
        </div>
      </div>
    </aside>
  );
}