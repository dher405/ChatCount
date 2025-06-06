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
    const [clientId, setClientId] = useState('');
    const [userId, setUserId] = useState('');
    const [startDate, setStartDate] = useState('');
    const [endDate, setEndDate] = useState('');
    const [logs, setLogs] = useState([]);
    const [results, setResults] = useState([]);
    const [loading, setLoading] = useState(false);

    // --- Constants ---
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
        logQueue.current.push({ message, type, time: new Date().toLocaleTimeString() });
    };
    
    /**
     * Handles the login process by redirecting to RingCentral.
     */
    const handleLogin = async () => {
        if (!clientId) {
            logMessage('Client ID is required.', 'error');
            setLogs(prev => [...prev, ...logQueue.current]); // Force immediate update for error
            logQueue.current = [];
            return;
        }

        sessionStorage.setItem('rc_client_id', clientId);
        const codeVerifier = generateCodeVerifier();
        sessionStorage.setItem('rc_code_verifier', codeVerifier);
        const codeChallenge = await generateCodeChallenge(codeVerifier);

        const authUrl = `${RC_AUTH_SERVER}/restapi/oauth/authorize?response_type=code&client_id=${clientId}&redirect_uri=${encodeURIComponent(REDIRECT_URI)}&code_challenge=${codeChallenge}&code_challenge_method=S256`;
        window.location.href = authUrl;
    };

    /**
     * Handles the logout process.
     */
    const handleLogout = () => {
        setAccessToken(null);
        sessionStorage.clear();
        logMessage('Logged out successfully.', 'info');
        setLogs(prev => [...prev, ...logQueue.current]);
        logQueue.current = [];
    };

    /**
     * Exchanges the authorization code for an access token.
     */
    const exchangeCodeForToken = async (code) => {
        const storedClientId = sessionStorage.getItem('rc_client_id');
        const codeVerifier = sessionStorage.getItem('rc_code_verifier');
        
        if (!code || !storedClientId || !codeVerifier) {
            logMessage('OAuth callback data missing from session.', 'error');
            return;
        }
        
        logMessage('Exchanging authorization code for access token...', 'info');
        
        const tokenUrl = `${RC_API_SERVER}/restapi/oauth/token`;
        const body = new URLSearchParams();
        body.append('grant_type', 'authorization_code');
        body.append('code', code);
        body.append('redirect_uri', REDIRECT_URI);
        body.append('client_id', storedClientId);
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
            
            // Clean up URL
            window.history.replaceState({}, document.title, window.location.pathname);
        } catch (error) {
            logMessage(error.message, 'error');
        } finally {
            setLogs(prev => [...prev, ...logQueue.current]); // Final log update
            logQueue.current = [];
        }
    };

    /**
     * Generic fetch wrapper for RingCentral API.
     */
    const rcFetch = async (endpoint) => {
        const token = sessionStorage.getItem('rc_access_token');
        if (!token) throw new Error("Not authenticated.");
        const url = `${RC_API_SERVER}${endpoint}`;
        const headers = { 'Authorization': `Bearer ${token}`, 'Accept': 'application/json' };
        
        const response = await fetch(url, { headers });

        if (response.status === 401) {
             handleLogout();
             throw new Error(`Authentication error (401). Please log in again.`);
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
            logMessage(`Fetching chat page ${page}...`, 'info');
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
                        logMessage(`Reached posts older than start date for chat ${chatId}. Stopping scan.`, 'warn');
                        return false;
                    }
                    if (post.creatorId === postUserId && postTime <= postEndDate) return true;
                }
                pageToken = data.navigation?.nextPageToken || null;
            } catch (error) {
                if (error.message.includes("404")) {
                    logMessage(`Cannot access posts for chat ${chatId} (permissions?). Skipping.`, 'warn');
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
        setLoading(true);

        const postStartDate = new Date(startDate);
        const postEndDate = new Date(endDate);
        postEndDate.setHours(23, 59, 59, 999);

        if (!userId || !startDate || !endDate) {
            logMessage('Please fill in all search fields.', 'error');
            setLoading(false);
            setLogs(prev => [...prev, ...logQueue.current]);
            logQueue.current = [];
            return;
        }

        logMessage('Starting process...', 'info');

        try {
            logMessage('Fetching all chats for user...', 'info');
            const allChats = await fetchAllUserChats();
            logMessage(`Found ${allChats.length} total chats to scan.`, 'info');

            let foundChatsList = [];
            for (const [index, chat] of allChats.entries()) {
                logMessage(`[${index + 1}/${allChats.length}] Scanning chat: ${chat.id} (${chat.type || 'N/A'})...`, 'info');
                const hasMessaged = await checkChatForUserPost(chat.id, userId, postStartDate, postEndDate);
                if (hasMessaged) {
                    logMessage(`User activity found in chat: ${chat.id}`, 'success');
                    foundChatsList.push(chat);
                    setResults([...foundChatsList]); // Update results as they are found
                }
            }
            logMessage(`Scan complete. Found user activity in ${foundChatsList.length} chats.`, 'success');
        } catch (error) {
            logMessage(error.message, 'error');
        } finally {
            setLoading(false);
            setLogs(prev => [...prev, ...logQueue.current]); // Final log update
            logQueue.current = [];
        }
    };
    
    const getLogItemClass = (type) => {
        const baseClass = 'log-item pl-3 py-1';
        switch (type) {
            case 'success': return `${baseClass} border-green-500`;
            case 'error': return `${baseClass} border-red-500`;
            case 'warn': return `${baseClass} border-orange-400`;
            default: return `${baseClass} border-blue-500`;
        }
    };
    
    // --- Render Logic ---
    if (!accessToken) {
        return (
             <div className="container mx-auto p-4 md:p-8 max-w-lg">
                <div className="bg-white p-6 rounded-xl shadow-md mb-8">
                     <h2 className="text-xl font-semibold mb-4 text-center">Login to RingCentral</h2>
                     <div className="space-y-4">
                        <div>
                            <label htmlFor="clientId" className="block text-sm font-medium text-gray-700 mb-1">Client ID</label>
                            <input type="text" id="clientId" value={clientId} onChange={e => setClientId(e.target.value)} className="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500" placeholder="Your RingCentral App Client ID" />
                        </div>
                    </div>
                    <div className="mt-6 text-center">
                         <button onClick={handleLogin} className="w-full md:w-auto bg-orange-500 text-white font-bold py-2 px-6 rounded-lg hover:bg-orange-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500 transition-colors">
                            Login with RingCentral
                        </button>
                    </div>
                    <p className="text-xs text-gray-500 mt-4 text-center">Note: In your RingCentral App settings, ensure your Redirect URI is set to this page's URL.</p>
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
                        <label htmlFor="userId" className="block text-sm font-medium text-gray-700 mb-1">User ID (creatorId)</label>
                        <input type="text" id="userId" value={userId} onChange={e => setUserId(e.target.value)} className="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500" placeholder="e.g., 123456789" />
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

            <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-8">
                <div className="bg-white p-6 rounded-xl shadow-md">
                    <h2 className="text-xl font-semibold mb-4">Found Chats ({results.length})</h2>
                    <div className="space-y-2 max-h-96 overflow-y-auto">
                        {results.map(chat => (
                            <div key={chat.id} className="p-2 bg-green-50 border-l-4 border-green-500 rounded-r-md">
                                <b>Chat ID:</b> {chat.id} <br /> <b>Type:</b> {chat.type || 'Direct'}
                            </div>
                        ))}
                    </div>
                </div>
                <div className="bg-white p-6 rounded-xl shadow-md">
                    <h2 className="text-xl font-semibold mb-4">Logs</h2>
                    <div className="space-y-2 text-sm max-h-96 overflow-y-auto font-mono">
                         {logs.map((log, index) => (
                            <div key={index} className={getLogItemClass(log.type)}>
                                [{log.time}] {log.message}
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}

export default App;
