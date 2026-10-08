import { Link, useLocation } from 'react-router-dom';
import {
    LayoutDashboard,
    Package,
    Sparkles,
    Search,
    ClipboardList,
    Wrench,
    Camera,
    TrendingUp,
    Flame,
    CalendarCheck
} from 'lucide-react';



export default function Sidebar() {
  const location = useLocation();

  const menuItems = [
      { name: 'Tableau de bord',   path: '/',                  icon: LayoutDashboard },
      { name: 'Liquidation',       path: '/liquidation',       icon: Flame },
      { name: 'Sourcing',          path: '/sourcing',          icon: Search },
      { name: 'Préparer Annonces', path: '/preparer-annonces', icon: Camera },
      { name: 'Achats',            path: '/achats',            icon: Package },
      { name: 'Ventes',            path: '/ventes',            icon: TrendingUp },
      { name: 'Générateur',        path: '/generateur',        icon: Sparkles },
      { name: 'Historique',        path: '/historique',        icon: ClipboardList },
      { name: 'Journal',           path: '/journal',           icon: CalendarCheck },
      { name: 'Maintenance',       path: '/maintenance',       icon: Wrench },
  ]; 

  const isActive = (path) => location.pathname === path;

  return (
    <aside className="w-64 h-full rounded-3xl shrink-0 bg-white shadow-soft">
      {/* Header du Menu */}
      <div className="w-full flex flex-col px-6 py-6
        rounded-3xl
        overflow-hidden">
        <h1 className="text-xl font-black tracking-tighter">
          VINTED<span className="text-brand-500">PRO</span>
        </h1>
        <p className="text-[10px] text-slate-400 font-bold uppercase tracking-widest mt-1">
          Gestionnaire de Stock
        </p>
      </div>

      {/* Liens de navigation */}
      <nav className="flex-1 space-y-2 px-4 py-4 overflow-y-auto">
        {menuItems.map((item) => {
          const Icon = item.icon; // On définit le composant icône

          return (
            <Link
              key={item.path}
              to={item.path}
              className={`flex items-center gap-4 pl-4 pr-4 py-3 rounded-2xl font-semibold transition-all duration-200 ${
                isActive(item.path)
                  ? 'bg-brand-50 text-brand-700'
                  : 'text-slate-500 hover:bg-slate-50 hover:text-brand-600'
              }`}
            >
              <Icon
                size={20}
                strokeWidth={isActive(item.path) ? 2.5 : 2}
                className={isActive(item.path) ? 'text-brand-600' : 'text-slate-400'}
              />
              <span className="text-sm">{item.name}</span>
            </Link>
          );
        })}
      </nav>

      {/* Footer du Menu (Statut API) */}
      <div className="p-6 border-t border-slate-100">
        <div className="flex items-center gap-3 bg-slate-50 p-3 rounded-2xl border border-slate-100">
          <div className="h-2 w-2 bg-green-500 rounded-full animate-pulse"></div>
          <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-tighter">
            Système Opérationnel
          </span>
        </div>
      </div>
    </aside>
  );
}