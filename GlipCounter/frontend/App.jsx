import React, { useState, useEffect, useRef } from 'react';
import { BrowserRouter as Router, Routes, Route, useNavigate } from 'react-router-dom';

function TrackPostsApp() {
  const [sessionId, setSessionId] = useState(localStorage.getItem('sessionId') || '');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [userId, setUserId] = useState('');
  const [discoveredRooms, setDiscoveredRooms] = useState([]);
  const [selectedRooms, setSelectedRooms] = useState([]);
  const [results, setResults] = useState(null);
  const [logs, setLogs] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const returnedSessionId = urlParams.get('sessionId');
    if (returnedSessionId) {
      localStorage.setItem('sessionId', returnedSessionId);
      setSessionId(returnedSessionId);
      window.history.replaceState(null, '', window.location.pathname);
    }
  }, []);

  const handleOAuthLogin = async () => {
    const res = await fetch('http://localhost:8000/oauth');
    const data = await res.json();
    setSessionId(data.sessionId);
    localStorage.setItem('sessionId', data.sessionId);
    window.location.href = data.auth_url;
  };

  const handleDiscoverRooms = async () => {
    setLogs([]);
    setError('');
    setDiscoveredRooms([]);
    setSelectedRooms([]);
    setResults(null);

  const payload = {
    startDate,
    endDate,
    userIds: userId.split(',').map(id => id.trim()),
    sessionId,
    roomIds: selectedRooms,  // <- this must be `roomIds` instead of `meetingRooms`
  };


    try {
      const res = await fetch('http://localhost:8000/api/discover-meeting-rooms', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        setError(data.error || 'Failed to discover meeting rooms.');
      } else {
        const rooms = Object.entries(data.rooms).map(([id, name]) => ({ id, name }));
        setDiscoveredRooms(rooms);
      }
      setLogs(data.logs || []);
    } catch (err) {
      setError('Failed to fetch rooms');
      setLogs(prev => [...prev, `❗ Fetch error: ${err.message}`]);
    }
  };

  const handleSubmit = async () => {
    setLoading(true);
    setError('');
    setResults(null);
    setLogs([]);

    const payload = {
      startDate,
      endDate,
      userIds: userId.split(',').map(id => id.trim()),
      sessionId,
      roomIds: selectedRooms,
    };

    try {
      const res = await fetch('http://localhost:8000/api/track-posts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        setError(data.error || 'Unknown error');
      } else {
        setResults(data.posts);
      }
      setLogs(data.logs || []);
    } catch (err) {
      setError('Failed to fetch data');
      setLogs(prev => [...prev, `❗ Fetch error: ${err.message}`]);
    }

    setLoading(false);
  };

  const handleLogout = () => {
    localStorage.removeItem('sessionId');
    setSessionId('');
    setResults(null);
    setLogs([]);
    setStartDate('');
    setEndDate('');
    setUserId('');
    setDiscoveredRooms([]);
    setSelectedRooms([]);
    setError('');
  };

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-3xl font-bold text-blue-600 mb-6">RingCentral Post Tracker</h1>

      {!sessionId ? (
        <button
          onClick={handleOAuthLogin}
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
        >
          Login with RingCentral
        </button>
      ) : (
        <>
          <button
            onClick={handleLogout}
            className="mb-4 px-3 py-1 bg-gray-400 text-white rounded hover:bg-gray-500"
          >
            Logout
          </button>
          <div className="space-y-4">
            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className="w-full p-2 border rounded" />
            <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className="w-full p-2 border rounded" />
            <input type="text" value={userId} onChange={e => setUserId(e.target.value)} className="w-full p-2 border rounded" placeholder="User IDs (comma-separated)" />
            <button onClick={handleDiscoverRooms} className="px-4 py-2 bg-indigo-500 text-white rounded hover:bg-indigo-600">
              Discover Meeting Rooms
            </button>
          </div>

          {discoveredRooms.length > 0 && (
            <div className="mt-6">
              <h2 className="text-lg font-semibold mb-2">Select Meeting Rooms</h2>
              <div className="space-y-2">
                {discoveredRooms.map(room => (
                  <div key={room.id} className="flex items-center space-x-2">
                    <input
                      type="checkbox"
                      id={`room-${room.id}`}
                      value={room.id}
                      checked={selectedRooms.includes(room.id)}
                      onChange={e => {
                        const checked = e.target.checked;
                        setSelectedRooms(prev =>
                          checked ? [...prev, room.id] : prev.filter(id => id !== room.id)
                        );
                      }}
                      className="form-checkbox"
                    />
                    <label htmlFor={`room-${room.id}`} className="text-sm font-medium">
                      {`${room.id} - ${room.name}`}
                    </label>
                  </div>
                ))}
              </div>
              <button onClick={handleSubmit} disabled={loading || selectedRooms.length === 0} className="mt-4 px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700">
                {loading ? 'Tracking...' : 'Track Posts'}
              </button>
            </div>
          )}
        </>
      )}

      {error && <div className="mt-4 text-red-600 font-medium border border-red-300 p-3 rounded">❌ {error}</div>}

      {results && (
        <div className="mt-6 overflow-x-auto">
          <h2 className="text-xl font-semibold mb-4">Results</h2>
          <table className="min-w-full table-auto border-collapse border border-gray-300">
            <thead>
              <tr>
                <th className="border p-2 bg-gray-100">Room ↓ / User →</th>
                {[...new Set(Object.values(results).flatMap(userMap => Object.keys(userMap)))].map(user => (
                  <th key={user} className="border p-2 bg-gray-100">
                    {user}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(results).map(([room, userMap]) => (
                <tr key={room}>
                  <td className="border p-2 font-medium">{room}</td>
                  {[...new Set(Object.values(results).flatMap(userMap => Object.keys(userMap)))].map(user => (
                    <td key={user} className="border p-2 text-center">
                      {userMap[user] || 0}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {logs.length > 0 && (
        <div className="mt-6">
          <h2 className="text-lg font-semibold mb-2">Logs</h2>
          <div className="bg-gray-100 border border-gray-300 p-4 rounded text-sm font-mono whitespace-pre-wrap">
            {logs.map((log, idx) => (
              <div key={idx}>{log}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function OAuthCallback() {
  const navigate = useNavigate();
  const didRun = useRef(false);

  useEffect(() => {
    if (didRun.current) return;
    didRun.current = true;

    const params = new URLSearchParams(window.location.search);
    const sessionId = params.get('state');
    const code = params.get('code');

    if (sessionId && code) {
      fetch(`http://localhost:8000/oauth/callback?code=${code}&state=${sessionId}`)
        .then(res => {
          if (!res.ok) throw new Error('OAuth callback failed');
          return res.text();
        })
        .then(() => {
          localStorage.setItem('sessionId', sessionId);
          navigate('/');
        })
        .catch(err => {
          console.error('OAuth callback failed:', err);
          navigate('/error');
        });
    } else {
      navigate('/error');
    }
  }, [navigate]);

  return <div className="p-4 text-lg">Finalizing login...</div>;
}

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<TrackPostsApp />} />
        <Route path="/oauth2callback" element={<OAuthCallback />} />
        <Route path="/oauth-success" element={<div className="p-4">✅ Login successful!</div>} />
        <Route path="/error" element={<div className="p-4 text-red-500">❌ OAuth failed.</div>} />
      </Routes>
    </Router>
  );
}

export default App;
