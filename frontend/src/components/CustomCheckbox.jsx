import React, { useState } from 'react';
import { Check } from 'lucide-react';

const CustomCheckbox = ({ id = "my-checkbox", name, checked, onChange }) => {
  return (
    <label 
      htmlFor={id} 
      className="inline-flex items-center gap-3 cursor-pointer select-none"
    >
      {/* 1. Le VRAI input : caché visuellement mais lisible par le navigateur/clavier */}
      <input
        type="checkbox"
        id={id}
        name={name}
        className="sr-only" // Classe Tailwind pour cacher (Screen Reader Only)
        checked={checked}
        onChange={onChange}
      />

      {/* 2. Le FAUX input : purement visuel */}
      <div
        className={`
          flex items-center justify-center w-6 h-6 rounded-md transition-colors duration-200
          ${checked ? 'bg-[#21C55D]' : 'bg-gray-200 border border-gray-300'}
        `}
      >
        {/* On n'affiche l'icône Lucid que si la case est cochée */}
        {checked && (
          <Check className="w-4 h-4 text-white" strokeWidth={3} />
        )}
      </div>
    </label>
  );
};

export default CustomCheckbox;