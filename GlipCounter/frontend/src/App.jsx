import React, { useState, useEffect, useRef } from 'react';

function App() {
    // --- State Management ---
    const [sessionId, setSessionId] = useState(localStorage.getItem('sessionId') || null);
    const [userId, setUserId] = useState('');
    const [startDate, setStartDate] = useState('');
    const [endDate, setEndDate] = useState('');
    const [logs, setLogs] = useState([]);
    const [results, setResults] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    // --- Backend Endpoint ---
    const BACKEND_URL = 'https://chatcount.onrender.com';

    // --- Effects ---
    // Effect to initialize dates and handle OAuth callback by checking for sessionId in the URL
    useEffect(() => {
        // Set default dates
        const today = new Date();
        const thirtyDaysAgo = new Date();
        thirtyDaysAgo.setDate(today.getDate() - 30);
        setEndDate(today.toISOString().split('T')[0]);
        setStartDate(thirtyDaysAgo.toISOString().split('T')[0]);

        // This handles the redirect from your backend after successful OAuth
        const urlParams = new URLSearchParams(window.location.search);
        const returnedSessionId = urlParams.get('sessionId');
        if (returnedSessionId) {
            localStorage.setItem('sessionId', returnedSessionId);
            setSessionId(returnedSessionId);
            // Clean up URL
            window.history.replaceState(null, '', window.location.pathname);
        }
    }, []);

    // --- Core Functions ---

    /**
     * Handles the login process by calling the backend to get the auth URL.
     */
    const handleLogin = async () => {
        try {
            const res = await fetch(`${BACKEND_URL}/oauth`);
            if (!res.ok) {
                 const errData = await res.json();
                 throw new Error(errData.error || 'Failed to start OAuth process.');
            }
            const data = await res.json();
            // The backend provides the sessionId and the URL to redirect the user to
            localStorage.setItem('sessionId', data.sessionId);
            setSessionId(data.sessionId);
            window.location.href = data.auth_url;
        } catch (err) {
            setError(`Login failed: ${err.message}`);
        }
    };

    /**
     * Handles the logout process.
     */
    const handleLogout = () => {
        localStorage.removeItem('sessionId');
        setSessionId(null);
        setResults([]);
        setLogs([]);
        setError('');
    };
    
    /**
     * Main application logic to find chats by calling the backend.
     */
    const findChats = async () => {
        setResults([]);
        setLogs([]);
        setError('');
        setLoading(true);

        if (!userId || !startDate || !endDate) {
            setError('Please fill in all search fields.');
            setLoading(false);
            return;
        }

        const payload = {
            startDate,
            endDate,
            userIds: userId.split(',').map(id => id.trim()),
            sessionId,
        };

        try {
            // This endpoint in your old script appears to perform the search.
            const res = await fetch(`${BACKEND_URL}/api/discover-meeting-rooms`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });

            const data = await res.json();
            setLogs(data.logs || []);

            if (!res.ok) {
                throw new Error(data.error || 'Failed to discover meeting rooms.');
            }
            
            // The backend returns an object of rooms; we'll format it for display.
            const foundRooms = Object.entries(data.rooms).map(([id, name]) => ({ id, name, type: 'Team' }));
            setResults(foundRooms);

        } catch (err) {
            setError(`Search failed: ${err.message}`);
        } finally {
            setLoading(false);
        }
    };
    
    // --- Render Logic ---
    if (!sessionId) {
        return (
             <div className="container mx-auto p-4 md:p-8 max-w-lg">
                <div className="bg-white p-6 rounded-xl shadow-md mb-8">
                     <h2 className="text-xl font-semibold mb-4 text-center">Login to RingCentral</h2>
                     <div className="mt-6 text-center">
                         <button onClick={handleLogin} className="w-full md:w-auto bg-orange-500 text-white font-bold py-2 px-6 rounded-lg hover:bg-orange-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500 transition-colors">
                            Login with RingCentral
                        </button>
                    </div>
                    {error && <p className="mt-4 text-center text-red-600">{error}</p>}
                </div>
            </div>
        );
    }
    
    return (
        <div className="container mx-auto p-4 md:p-8 max-w-4xl">
            <header className="mb-8 text-center">
                <h1 className="text-3xl md:text-4xl font-bold text-gray-900">RingCentral User Chat Finder</h1>
                <p className="mt-2 text-gray-600">Find all chats a user has messaged in a specific date range.</p>
            </header>

            <div className="bg-white p-6 rounded-xl shadow-md">
                <div className="flex justify-between items-center mb-4">
                    <h2 className="text-xl font-semibold">Search Controls</h2>
                    <button onClick={handleLogout} className="text-sm text-red-600 hover:underline">Logout</button>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div>
                        <label htmlFor="userId" className="block text-sm font-medium text-gray-700 mb-1">User IDs (comma-separated)</label>
                        <input type="text" id="userId" value={userId} onChange={e => setUserId(e.target.value)} className="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500" placeholder="e.g., 123, 456" />
                    </div>
                    <div></div>
                    <div>
                        <label htmlFor="startDate" className="block text-sm font-medium text-gray-700 mb-1">Start Date</label>
                        <input type="date" id="startDate" value={startDate} onChange={e => setStartDate(e.target.value)} className="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500" />
                    </div>
                    <div>
                        <label htmlFor="endDate" className="block text-sm font-medium text-gray-700 mb-1">End Date</label>
                        <input type="date" id="endDate" value={endDate} onChange={e => setEndDate(e.target.value)} className="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500" />
                    </div>
                </div>

                <div className="mt-6 text-center">
                    <button onClick={findChats} disabled={loading} className="w-full md:w-auto bg-blue-600 text-white font-bold py-2 px-6 rounded-lg hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:bg-gray-400 transition-colors">
                        {loading ? 'Searching...' : 'Find Chats'}
                    </button>
                </div>
            </div>

            {error && <div className="mt-6 p-4 text-red-700 bg-red-100 border border-red-400 rounded-md"><b>Error:</b> {error}</div>}

            <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-8">
                <div className="bg-white p-6 rounded-xl shadow-md">
                    <h2 className="text-xl font-semibold mb-4">Found Chats ({results.length})</h2>
                    <div className="space-y-2 max-h-96 overflow-y-auto">
                        {results.length > 0 ? results.map(chat => (
                            <div key={chat.id} className="p-2 bg-green-50 border-l-4 border-green-500 rounded-r-md">
                                <b>Chat ID:</b> {chat.id} <br /> <b>Name:</b> {chat.name || 'N/A'}
                            </div>
                        )) : <p className="text-gray-500">No chats found, or search not yet run.</p>}
                    </div>
                </div>
                <div className="bg-white p-6 rounded-xl shadow-md">
                    <h2 className="text-xl font-semibold mb-4">Logs</h2>
                    <div className="space-y-2 text-sm max-h-96 overflow-y-auto font-mono bg-gray-50 p-3 rounded-md">
                         {logs.length > 0 ? logs.map((log, index) => (
                            <div key={index} className="whitespace-pre-wrap">{log}</div>
                        )) : <p className="text-gray-500">No logs to display.</p>}
                    </div>
                </div>
            </div>
        </div>
    );
}

export default App;
