import React from 'react';
import { ExternalLink, Trash2, Clock, Heart } from 'lucide-react';

const ProductCardSniper = ({ product, onDelete, activeFilter }) => {
  const imageUrl = product.photos?.[0]?.url || 'https://via.placeholder.com/300x400';
  const filter = activeFilter?.nom || "Filtre introuvable"

  return (
    <div className="w-full flex bg-white rounded-3xl border border-slate-200 overflow-hidden shadow-sm hover:shadow-md transition-all h-[420px]">
      
      {/* SECTION GAUCHE : IMAGE ET ACTIONS */}
      <div className="w-[220px] p-3 flex flex-col gap-3 shrink-0 h-full">
        <div className="relative flex-1 bg-slate-100 rounded-2xl overflow-hidden">
          <img 
            src={imageUrl} 
            alt={product.title}
            className="w-full h-full object-cover"
          />
        </div>
        <div className="flex gap-2">
          <button 
            onClick={() => onDelete(product.id)}
            className="flex-1 py-2 flex justify-center bg-white border border-slate-200 rounded-xl text-red-500 hover:bg-red-50 transition-colors"
          >
            <Trash2 size={18} />
          </button>
          <a 
            href={product.url} 
            target="_blank" 
            rel="noopener noreferrer"
            className="flex-1 py-2 flex justify-center bg-white border border-slate-200 rounded-xl text-slate-600 hover:bg-slate-50 transition-colors"
          >
            <ExternalLink size={18} />
          </a>
        </div>
      </div>

      {/* SECTION CENTRALE : INFOS PRODUIT */}
      <div className="flex-1 p-6 flex flex-col justify-between border-x border-slate-100">
        <div>
          <div className="flex justify-between items-start mb-2">
            <span className="px-3 py-1 bg-cyan-400 text-white text-[10px] font-black rounded-lg uppercase">
              {filter}
            </span>
            <span className="flex items-center gap-1 text-[11px] font-bold text-slate-400">
              <Clock size={12} /> {product.relative_time || "À l'instant"}
            </span>
          </div>
          
          <h4 className="text-xs font-bold text-slate-400 uppercase tracking-tight mb-1">
            {product.brand_title}
          </h4>
          <h2 className="text-xl font-black text-slate-800 leading-tight mb-4">
            {product.title}
          </h2>

          <div className="flex gap-6 mb-4">
            <div>
              <p className="text-lg font-black text-slate-800">{product.size_title || 'N/A'}</p>
              <p className="text-[10px] font-bold text-slate-400 uppercase">Taille</p>
            </div>
            <div>
              <p className="text-lg font-black text-slate-800">Très bon</p>
              <p className="text-[10px] font-bold text-slate-400 uppercase">ETAT</p>
            </div>
          </div>
        </div>
      </div>

      {/* SECTION DROITE : RENTABILITÉ (Le bloc gris) */}
      <div className="w-1/3 bg-slate-50/80 p-6 flex flex-col justify-between">
        <div className="text-center">
          <p className="text-[11px] font-bold text-slate-400 uppercase mb-1">Total Investi</p>
          <p className="text-3xl font-black text-slate-900 mb-1">
            {Number(product.total_invested).toFixed(2)} €
          </p>
          <p className="text-[11px] font-bold text-slate-500 italic">
            Rentabilité (Est.): <span className="text-emerald-600">+45 €</span>
          </p>
        </div>

        <div className="space-y-2 border-t border-slate-200 pt-4">
          <div className="flex justify-between text-xs font-bold text-slate-600">
            <span className="text-slate-400 uppercase text-[10px]">Prix article:</span>
            <span>{Number(product.total_invested).toFixed(2)} €</span>
          </div>
          <div className="flex justify-between text-xs font-bold text-slate-600">
            <span className="text-slate-400 uppercase text-[10px]">Livraison:</span>
            <span>{Number(product.total_invested).toFixed(2)} €</span>
          </div>
          <div className="flex justify-between text-xs font-bold text-slate-600">
            <span className="text-slate-400 uppercase text-[10px]">Service:</span>
            <span>{Number(product.total_invested).toFixed(2)} €</span>
          </div>
        </div>

        <div className="mt-4 pt-3 border-t border-slate-200 flex justify-between items-center">
          <span className="text-[10px] font-black text-slate-400 uppercase">Frais totaux:</span>
          <span className="text-sm font-black text-slate-800">
            {(product.shipping_est + product.service_fee).toFixed(2)} €
          </span>
        </div>
      </div>
    </div>
  );
};

export default ProductCardSniper;