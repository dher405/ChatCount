
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RingCentral User Chat Finder</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: 'Inter', sans-serif;
        }
        .log-item {
            border-left-width: 4px;
        }
        .log-item-info { border-color: #3b82f6; }
        .log-item-success { border-color: #22c55e; }
        .log-item-error { border-color: #ef4444; }
        .log-item-warn { border-color: #f97316; }
        .hidden {
            display: none;
        }
    </style>
</head>
<body class="bg-gray-50 text-gray-800">

    <div class="container mx-auto p-4 md:p-8 max-w-4xl">
        <header class="mb-8 text-center">
            <h1 class="text-3xl md:text-4xl font-bold text-gray-900">RingCentral User Chat Finder</h1>
            <p class="mt-2 text-gray-600">Find all chats a user has messaged in a specific date range.</p>
        </header>

        <!-- Auth Section -->
        <div id="authSection" class="bg-white p-6 rounded-xl shadow-md mb-8">
             <h2 class="text-xl font-semibold mb-4 text-center">Login to RingCentral</h2>
             <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div class="md:col-span-2">
                    <label for="clientId" class="block text-sm font-medium text-gray-700 mb-1">Client ID</label>
                    <input type="text" id="clientId" class="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500" placeholder="Your RingCentral App Client ID">
                </div>
            </div>
            <div class="mt-6 text-center">
                 <button id="loginBtn" class="w-full md:w-auto bg-orange-500 text-white font-bold py-2 px-6 rounded-lg hover:bg-orange-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500 transition-colors">
                    Login with RingCentral
                </button>
            </div>
            <p class="text-xs text-gray-500 mt-4 text-center">Note: In your RingCentral App settings, ensure your Redirect URI is set to this page's URL.</p>
        </div>


        <!-- App Section -->
        <div id="appSection" class="hidden">
            <div class="bg-white p-6 rounded-xl shadow-md">
                <div class="flex justify-between items-center mb-4">
                    <h2 class="text-xl font-semibold">Search Controls</h2>
                    <button id="logoutBtn" class="text-sm text-red-600 hover:underline">Logout</button>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div>
                        <label for="userId" class="block text-sm font-medium text-gray-700 mb-1">User ID (creatorId)</label>
                        <input type="text" id="userId" class="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500" placeholder="e.g., 123456789">
                    </div>
                    <div></div>
                    <div>
                        <label for="startDate" class="block text-sm font-medium text-gray-700 mb-1">Start Date</label>
                        <input type="date" id="startDate" class="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                    </div>
                    <div>
                        <label for="endDate" class="block text-sm font-medium text-gray-700 mb-1">End Date</label>
                        <input type="date" id="endDate" class="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                    </div>
                </div>

                <div class="mt-6 text-center">
                    <button id="findChatsBtn" class="w-full md:w-auto bg-blue-600 text-white font-bold py-2 px-6 rounded-lg hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:bg-gray-400 transition-colors">
                        Find Chats
                    </button>
                </div>
            </div>

            <!-- Results and Logs -->
            <div class="mt-8 grid grid-cols-1 md:grid-cols-2 gap-8">
                <div class="bg-white p-6 rounded-xl shadow-md">
                    <h2 class="text-xl font-semibold mb-4">Found Chats (<span id="resultCount">0</span>)</h2>
                    <div id="results" class="space-y-2 max-h-96 overflow-y-auto"></div>
                </div>
                <div class="bg-white p-6 rounded-xl shadow-md">
                    <h2 class="text-xl font-semibold mb-4">Logs</h2>
                    <div id="logs" class="space-y-2 text-sm max-h-96 overflow-y-auto font-mono"></div>
                </div>
            </div>
        </div>
    </div>

<script>
    // DOM Elements
    const authSection = document.getElementById('authSection');
    const appSection = document.getElementById('appSection');
    const loginBtn = document.getElementById('loginBtn');
    const logoutBtn = document.getElementById('logoutBtn');
    const findChatsBtn = document.getElementById('findChatsBtn');
    const clientIdInput = document.getElementById('clientId');
    const userIdInput = document.getElementById('userId');
    const startDateInput = document.getElementById('startDate');
    const endDateInput = document.getElementById('endDate');
    const resultsDiv = document.getElementById('results');
    const logsDiv = document.getElementById('logs');
    const resultCountSpan = document.getElementById('resultCount');
    
    // Constants
    const RC_API_SERVER = 'https://platform.ringcentral.com';
    const RC_AUTH_SERVER = 'https://platform.ringcentral.com';
    const REDIRECT_URI = window.location.origin + window.location.pathname;


    // App state
    let accessToken = null;

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

    /**
     * Logs messages to the UI.
     */
    function logMessage(message, type = 'info') {
        const logItem = document.createElement('div');
        logItem.className = `log-item log-item-${type} pl-3 py-1`;
        logItem.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
        logsDiv.appendChild(logItem);
        logsDiv.scrollTop = logsDiv.scrollHeight;
    }

    /**
     * Handles the login process by redirecting to RingCentral.
     */
    async function handleLogin() {
        const clientId = clientIdInput.value.trim();
        if (!clientId) {
            logMessage('Client ID is required.', 'error');
            return;
        }

        sessionStorage.setItem('rc_client_id', clientId);

        const codeVerifier = generateCodeVerifier();
        sessionStorage.setItem('rc_code_verifier', codeVerifier);
        const codeChallenge = await generateCodeChallenge(codeVerifier);

        const authUrl = `${RC_AUTH_SERVER}/restapi/oauth/authorize?response_type=code&client_id=${clientId}&redirect_uri=${encodeURIComponent(REDIRECT_URI)}&code_challenge=${codeChallenge}&code_challenge_method=S256`;
        window.location.href = authUrl;
    }

    /**
     * Handles the logout process.
     */
    function handleLogout() {
        accessToken = null;
        sessionStorage.clear();
        authSection.classList.remove('hidden');
        appSection.classList.add('hidden');
        logMessage('Logged out successfully.', 'info');
    }
    
    /**
     * Exchanges the authorization code for an access token.
     */
    async function exchangeCodeForToken(code) {
        const clientId = sessionStorage.getItem('rc_client_id');
        const codeVerifier = sessionStorage.getItem('rc_code_verifier');
        
        if (!code || !clientId || !codeVerifier) {
            logMessage('OAuth callback data missing from session.', 'error');
            return;
        }

        const tokenUrl = `${RC_API_SERVER}/restapi/oauth/token`;
        const body = new URLSearchParams();
        body.append('grant_type', 'authorization_code');
        body.append('code', code);
        body.append('redirect_uri', REDIRECT_URI);
        body.append('client_id', clientId);
        body.append('code_verifier', codeVerifier);

        try {
            logMessage('Exchanging authorization code for access token...', 'info');
            const response = await fetch(tokenUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: body
            });

            if (!response.ok) {
                throw new Error(`Token exchange failed: ${response.statusText}`);
            }

            const data = await response.json();
            accessToken = data.access_token;
            sessionStorage.setItem('rc_access_token', accessToken);
            logMessage('Successfully obtained access token.', 'success');
            
            // Clean up URL and show app
            window.history.replaceState({}, document.title, window.location.pathname);
            authSection.classList.add('hidden');
            appSection.classList.remove('hidden');

        } catch (error) {
            logMessage(error.message, 'error');
        }
    }


    /**
     * Generic fetch wrapper for RingCentral API.
     */
    async function rcFetch(endpoint) {
        if (!accessToken) throw new Error("Not authenticated.");
        const url = `${RC_API_SERVER}${endpoint}`;
        const headers = { 'Authorization': `Bearer ${accessToken}`, 'Accept': 'application/json' };
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
    }
    
    /**
     * Main application logic to find chats.
     */
    async function findChats() {
        resultsDiv.innerHTML = '';
        logsDiv.innerHTML = '';
        resultCountSpan.textContent = '0';
        findChatsBtn.disabled = true;

        const userId = userIdInput.value.trim();
        const startDate = new Date(startDateInput.value);
        const endDate = new Date(endDateInput.value);
        endDate.setHours(23, 59, 59, 999);

        if (!userId || !startDateInput.value || !endDateInput.value) {
            logMessage('Please fill in all search fields.', 'error');
            findChatsBtn.disabled = false;
            return;
        }

        logMessage('Starting process...', 'info');

        try {
            logMessage('Fetching all chats for user...', 'info');
            const allChats = await fetchAllUserChats();
            logMessage(`Found ${allChats.length} total chats to scan.`, 'info');

            let foundChatsCount = 0;
            for (const [index, chat] of allChats.entries()) {
                logMessage(`[${index + 1}/${allChats.length}] Scanning chat: ${chat.id} (${chat.type || 'N/A'})...`, 'info');
                const hasMessaged = await checkChatForUserPost(chat.id, userId, startDate, endDate);
                if (hasMessaged) {
                    foundChatsCount++;
                    logMessage(`User activity found in chat: ${chat.id}`, 'success');
                    const resultItem = document.createElement('div');
                    resultItem.className = 'p-2 bg-green-50 border-l-4 border-green-500 rounded-r-md';
                    resultItem.innerHTML = `<b>Chat ID:</b> ${chat.id} <br> <b>Type:</b> ${chat.type || 'Direct'}`;
                    resultsDiv.appendChild(resultItem);
                    resultCountSpan.textContent = foundChatsCount;
                }
            }
            logMessage(`Scan complete. Found user activity in ${foundChatsCount} chats.`, 'success');
        } catch (error) {
            logMessage(error.message, 'error');
        } finally {
            findChatsBtn.disabled = false;
        }
    }


    /**
     * Fetches all chats a user is part of, handling pagination.
     */
    async function fetchAllUserChats() {
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
    }

    /**
     * Checks a single chat for posts by the user within the date range.
     */
    async function checkChatForUserPost(chatId, userId, startDate, endDate) {
        let pageToken = null;
        do {
            const endpoint = `/team-messaging/v1/chats/${chatId}/posts?recordCount=100${pageToken ? `&pageToken=${pageToken}` : ''}`;
            try {
                const data = await rcFetch(endpoint);
                if (!data.records || data.records.length === 0) return false;

                for (const post of data.records) {
                    const postTime = new Date(post.creationTime);
                    if (postTime < startDate) {
                        logMessage(`Reached posts older than start date for chat ${chatId}. Stopping scan.`, 'warn');
                        return false;
                    }
                    if (post.creatorId === userId && postTime <= endDate) return true;
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
    }
    
    // --- Initialization ---
    document.addEventListener('DOMContentLoaded', () => {
        // Set default dates
        const today = new Date();
        const thirtyDaysAgo = new Date();
        thirtyDaysAgo.setDate(today.getDate() - 30);
        endDateInput.value = today.toISOString().split('T')[0];
        startDateInput.value = thirtyDaysAgo.toISOString().split('T')[0];
        
        // Event Listeners
        loginBtn.addEventListener('click', handleLogin);
        logoutBtn.addEventListener('click', handleLogout);
        findChatsBtn.addEventListener('click', findChats);

        // Check for OAuth callback
        const urlParams = new URLSearchParams(window.location.search);
        const authCode = urlParams.get('code');

        if (authCode) {
            exchangeCodeForToken(authCode);
        } else {
            // Check for a stored token from a previous session
            const storedToken = sessionStorage.getItem('rc_access_token');
            if(storedToken) {
                accessToken = storedToken;
                authSection.classList.add('hidden');
                appSection.classList.remove('hidden');
                logMessage('Logged in from previous session.', 'info');
            }
        }
    });

</script>
</body>
</html>
