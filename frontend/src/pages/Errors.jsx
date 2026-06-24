import React, { useState, useEffect } from 'react';

const Errors = () => {
  const [errorLogs, setErrorLogs] = useState([
    { id: 1, time: '14:20', type: '401', message: 'Token ACCESS expiré, ré-authentification en cours' },
    { id: 2, time: '14:35', type: 'CAPTCHA', message: 'Vérification humaine requise par Vinted' }
  ]);

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-4">Journal des Erreurs</h1>
      <div className="bg-white shadow rounded-lg overflow-hidden">
        <table className="min-w-full">
          <thead className="bg-gray-100">
            <tr>
              <th className="px-6 py-3 text-left text-sm font-medium">Heure</th>
              <th className="px-6 py-3 text-left text-sm font-medium">Type</th>
              <th className="px-6 py-3 text-left text-sm font-medium">Message</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {errorLogs.map((log) => (
              <tr key={log.id} className="hover:bg-red-50">
                <td className="px-6 py-4 text-sm font-mono">{log.time}</td>
                <td className="px-6 py-4 text-sm font-bold text-red-600">{log.type}</td>
                <td className="px-6 py-4 text-sm text-gray-700">{log.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default Errors;