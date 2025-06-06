import React, { useState, useEffect, useRef } from 'react';

// --- Helper Functions for OAuth PKCE Flow ---
/**
 * PKCE Helper: Generates a random string for the code verifier.
 */
function generateCodeVerifier() {
    const randomByteArray = new Uint8Array(32);
    window.crypto.getRandomValues(randomByteArray);
    return btoa(String.fromCharCode.apply(null, randomByteArray))
        .replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

/**
 * PKCE Helper: Hashes and encodes the verifier to create the challenge.
 */
async function generateCodeChallenge(verifier) {
    const encoder = new TextEncoder();
    const data = encoder.encode(verifier);
    const digest = await window.crypto.subtle.digest('SHA-256', data);
    return btoa(String.fromCharCode.apply(null, new Uint8Array(digest)))
        .replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}


function App() {
    // --- State Management ---
    const [accessToken, setAccessToken] = useState(sessionStorage.getItem('rc_access_token') || null);
    const [userId, setUserId] = useState('');
    const [startDate, setStartDate] = useState('');
    const [endDate, setEndDate] = useState('');
    const [logs, setLogs] = useState([]);
    const [results, setResults] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    // --- Constants ---
    // IMPORTANT: Replace "YOUR_CLIENT_ID" with your actual RingCentral App Client ID.
    const CLIENT_ID = "YOUR_CLIENT_ID"; 
    const RC_API_SERVER = 'https://platform.ringcentral.com';
    const RC_AUTH_SERVER = 'https://platform.ringcentral.com';
    const REDIRECT_URI = window.location.origin + window.location.pathname;

    // --- Refs for avoiding re-renders on static values ---
    const logQueue = useRef([]);
    
    // --- Effects ---
    // Effect to initialize dates and handle OAuth callback
    useEffect(() => {
        // Set default dates
        const today = new Date();
        const thirtyDaysAgo = new Date();
        thirtyDaysAgo.setDate(today.getDate() - 30);
        setEndDate(today.toISOString().split('T')[0]);
        setStartDate(thirtyDaysAgo.toISOString().split('T')[0]);

        // Check for OAuth callback code in URL
        const urlParams = new URLSearchParams(window.location.search);
        const authCode = urlParams.get('code');

        if (authCode) {
            exchangeCodeForToken(authCode);
        }
    }, []);
    
    // Effect to batch log updates for performance
    useEffect(() => {
        const interval = setInterval(() => {
            if (logQueue.current.length > 0) {
                setLogs(prevLogs => [...prevLogs, ...logQueue.current]);
                logQueue.current = [];
            }
        }, 500); // Update logs every 500ms
        return () => clearInterval(interval);
    }, []);


    // --- Core Functions ---
    /**
     * Logs messages to a queue, which is then processed by a useEffect hook.
     */
    const logMessage = (message, type = 'info') => {
        const timestamp = new Date().toLocaleTimeString();
        logQueue.current.push(`[${timestamp}] ${message}`);
        // For immediate feedback on errors or completion
        if (type === 'error' || type === 'success') {
            setLogs(prev => [...prev, ...logQueue.current, `[${timestamp}] ${message}`]);
            logQueue.current = [];
        }
    };
    
    /**
     * Handles the login process by redirecting to RingCentral.
     */
    const handleLogin = async () => {
        if (CLIENT_ID === "YOUR_CLIENT_ID") {
            setError('Configuration needed: Please update the CLIENT_ID constant in the App.jsx source code with your RingCentral App Client ID.');
            return;
        }

        const codeVerifier = generateCodeVerifier();
        sessionStorage.setItem('rc_code_verifier', codeVerifier);
        const codeChallenge = await generateCodeChallenge(codeVerifier);

        const authUrl = `${RC_AUTH_SERVER}/restapi/oauth/authorize?response_type=code&client_id=${CLIENT_ID}&redirect_uri=${encodeURIComponent(REDIRECT_URI)}&code_challenge=${codeChallenge}&code_challenge_method=S256`;
        window.location.href = authUrl;
    };

    /**
     * Handles the logout process.
     */
    const handleLogout = () => {
        setAccessToken(null);
        sessionStorage.clear();
        setResults([]);
        setLogs(['Logged out successfully.']);
        setError('');
    };

    /**
     * Exchanges the authorization code for an access token.
     */
    const exchangeCodeForToken = async (code) => {
        const codeVerifier = sessionStorage.getItem('rc_code_verifier');
        
        if (!code || !codeVerifier) {
            setError('OAuth callback data missing from session.');
            return;
        }
        
        logMessage('Exchanging authorization code for access token...');
        
        const tokenUrl = `${RC_API_SERVER}/restapi/oauth/token`;
        const body = new URLSearchParams();
        body.append('grant_type', 'authorization_code');
        body.append('code', code);
        body.append('redirect_uri', REDIRECT_URI);
        body.append('client_id', CLIENT_ID);
        body.append('code_verifier', codeVerifier);

        try {
            const response = await fetch(tokenUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: body
            });

            if (!response.ok) throw new Error(`Token exchange failed: ${response.statusText}`);

            const data = await response.json();
            sessionStorage.setItem('rc_access_token', data.access_token);
            setAccessToken(data.access_token);
            logMessage('Successfully obtained access token.', 'success');
            
            window.history.replaceState(null, '', window.location.pathname);
        } catch (err) {
            setError(err.message);
        }
    };

    /**
     * Generic fetch wrapper for RingCentral API.
     */
    const rcFetch = async (endpoint) => {
        const token = sessionStorage.getItem('rc_access_token');
        if (!token) {
            handleLogout();
            throw new Error("Not authenticated. Please log in again.");
        }
        const url = `${RC_API_SERVER}${endpoint}`;
        const headers = { 'Authorization': `Bearer ${token}`, 'Accept': 'application/json' };
        
        const response = await fetch(url, { headers });

        if (response.status === 401) {
             handleLogout();
             throw new Error(`Authentication error (401). Your session may have expired. Please log in again.`);
        }
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`API Error ${response.status}: ${response.statusText}. Response: ${errorText}`);
        }
        return response.json();
    };
    
    /**
     * Fetches all chats a user is part of, handling pagination.
     */
    const fetchAllUserChats = async () => {
        let allChats = [];
        let pageToken = null;
        let page = 1;
        do {
            logMessage(`Fetching chat page ${page}...`);
            const endpoint = `/team-messaging/v1/chats?recordCount=250${pageToken ? `&pageToken=${pageToken}` : ''}`;
            const data = await rcFetch(endpoint);
            if (data.records) allChats = allChats.concat(data.records);
            pageToken = data.navigation?.nextPageToken || null;
            page++;
        } while (pageToken);
        return allChats;
    };
    
    /**
     * Checks a single chat for posts by the user within the date range.
     */
    const checkChatForUserPost = async (chatId, postUserId, postStartDate, postEndDate) => {
        let pageToken = null;
        do {
            const endpoint = `/team-messaging/v1/chats/${chatId}/posts?recordCount=100${pageToken ? `&pageToken=${pageToken}` : ''}`;
            try {
                const data = await rcFetch(endpoint);
                if (!data.records || data.records.length === 0) return false;

                for (const post of data.records) {
                    const postTime = new Date(post.creationTime);
                    if (postTime < postStartDate) {
                        logMessage(`Reached posts older than start date for chat ${chatId}. Stopping scan.`);
                        return false;
                    }
                    if (post.creatorId === postUserId && postTime <= postEndDate) return true;
                }
                pageToken = data.navigation?.nextPageToken || null;
            } catch (error) {
                if (error.message.includes("404")) {
                    logMessage(`Cannot access posts for chat ${chatId} (permissions?). Skipping.`);
                    return false;
                }
                throw error;
            }
        } while (pageToken);
        return false;
    };

    /**
     * Main application logic to find chats.
     */
    const findChats = async () => {
        setResults([]);
        setLogs([]);
        logQueue.current = [];
        setError('');
        setLoading(true);

        const postStartDate = new Date(startDate);
        const postEndDate = new Date(endDate);
        postEndDate.setHours(23, 59, 59, 999);
        
        const userIds = userId.split(',').map(id => id.trim()).filter(id => id);

        if (userIds.length === 0 || !startDate || !endDate) {
            setError('Please provide at least one User ID and select a valid date range.');
            setLoading(false);
            return;
        }
        
        logMessage('Starting process...', 'info');

        try {
            logMessage('Fetching all team chats...', 'info');
            const allChats = await fetchAllUserChats();
            logMessage(`Found ${allChats.length} total chats to scan.`, 'info');

            let foundChatsList = [];
            for (const chat of allChats) {
                 for (const currentUserId of userIds) {
                    logMessage(`[${chat.id}] Scanning for user ${currentUserId}...`);
                    const hasMessaged = await checkChatForUserPost(chat.id, currentUserId, postStartDate, postEndDate);
                    if (hasMessaged) {
                        logMessage(`User ${currentUserId} activity found in chat: ${chat.id}`, 'success');
                        // Avoid adding duplicate rooms if multiple users are found in the same room
                        if (!foundChatsList.some(found => found.id === chat.id)) {
                             foundChatsList.push({ id: chat.id, name: chat.name || 'Direct Chat/Group' });
                        }
                    }
                 }
            }
            setResults(foundChatsList);
            logMessage(`Scan complete. Found activity in ${foundChatsList.length} unique chats.`, 'success');
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };
    
    // --- Render Logic ---
    if (!accessToken) {
        return (
             <div className="container mx-auto p-4 md:p-8 max-w-lg">
                <div className="bg-white p-6 rounded-xl shadow-md mb-8">
                     <h2 className="text-xl font-semibold mb-4 text-center">Login to RingCentral2</h2>
                     <div className="mt-6 text-center">
                         <button onClick={handleLogin} className="w-full md:w-auto bg-orange-500 text-white font-bold py-2 px-6 rounded-lg hover:bg-orange-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500 transition-colors">
                            Login with RingCentral
                        </button>
                    </div>
                    {error && <p className="mt-4 text-center text-red-600 font-semibold">{error}</p>}
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
