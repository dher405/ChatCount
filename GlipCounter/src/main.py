from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from ringcentral import SDK
from datetime import datetime, timezone, timedelta
import os
import logging
from uuid import uuid4
from dotenv import load_dotenv
from typing import List, Dict
import urllib.parse
import json
import time
import asyncio

# Load environment variables from cred.env file
load_dotenv(dotenv_path='cred.env')

app = FastAPI()

# CORS config for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://chatcount-fe.onrender.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(name)s: %(message)s')
logger = logging.getLogger("main")

# Load credentials from environment variables
client_id = os.getenv("RC_CLIENT_ID")
client_secret = os.getenv("RC_CLIENT_SECRET")
server_url = os.getenv("RC_SERVER_URL", "https://platform.ringcentral.com")
redirect_uri = os.getenv("RC_REDIRECT_URI")

if not all([client_id, client_secret, redirect_uri]):
    raise EnvironmentError("Missing one or more RingCentral configuration values in .env")

TOKEN_STORE_FILE = 'token_store.json'

CACHE_TTL_SECONDS = 300  # 5 minutes TTL for cache

# Cache with expiry timestamps
meeting_room_cache: Dict[str, Dict] = {}
post_tracking_cache: Dict[str, Dict] = {}

# Token store functions
def load_tokens():
    try:
        with open(TOKEN_STORE_FILE, 'r') as f:
            tokens = json.load(f)
            logger.debug(f"🔐 Loaded {len(tokens)} tokens from disk.")
            return tokens
    except Exception as e:
        logger.warning(f"⚠️ Could not load token store: {e}")
        return {}

def save_tokens():
    try:
        with open(TOKEN_STORE_FILE, 'w') as f:
            json.dump(token_store, f)
        logger.debug(f"📂 Token store saved. Sessions stored: {list(token_store.keys())}")
    except Exception as e:
        logger.error(f"❌ Failed to save token store: {e}")

def get_platform(session_id: str, logs: List[str]):
    token_data = token_store.get(session_id)
    if not token_data:
        logs.append(f"❌ Session ID not found in token store: {session_id}")
        raise HTTPException(status_code=401, detail="Not authenticated with RingCentral")

    rcsdk = SDK(client_id, client_secret, server_url, redirect_uri)
    platform = rcsdk.platform()
    platform.auth().set_data(token_data)

    try:
        if platform.logged_in():
            platform.refresh()
            token_store[session_id] = platform.auth().data()
            save_tokens()
            logs.append(f"🔄 Refreshed token for session: {session_id}")
    except Exception as e:
        logs.append(f"⚠️ Token refresh failed: {e}")
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    return platform

async def ringcentral_get_with_retry(platform, url, logs, max_retries=3):
    wait_time = 1
    for attempt in range(max_retries):
        try:
            return platform.get(url)
        except Exception as e:
            if "CMN-301" in str(e):
                logs.append(f"⏳ Error occurred. Retrying in {wait_time} seconds... ({e})")
                await asyncio.sleep(wait_time)
                wait_time *= 2
            else:
                logs.append(f"⚠️ Non-rate limit error: {e}")
                raise
    logs.append(f"❌ Failed to retrieve data after {max_retries} retries: {e}")
    raise HTTPException(status_code=429, detail="Rate limit exceeded after retries")

# Load token store at startup
token_store = load_tokens()

class MeetingRoomDiscoveryRequest(BaseModel):
    startDate: str
    endDate: str
    userIds: List[str]
    sessionId: str

class TrackPostsRequest(BaseModel):
    startDate: str
    endDate: str
    meetingRooms: List[str]
    userIds: List[str]
    sessionId: str

@app.get("/ping")
def ping():
    return {"status": "awake"}

@app.get("/oauth")
def oauth_login():
    session_id = str(uuid4())
    auth_url = (
        f"{server_url}/restapi/oauth/authorize?"
        + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": session_id
        })
    )
    logger.info(f"Generated OAuth URL for session: {session_id}")
    return {"auth_url": auth_url, "sessionId": session_id}

@app.get("/oauth/callback")
def oauth_callback(code: str, state: str):
    try:
        logger.info(f"🔁 Received OAuth callback with code: {code}, state: {state}")
        rcsdk = SDK(client_id, client_secret, server_url, redirect_uri)
        platform = rcsdk.platform()
        platform.login(code=code, redirect_uri=redirect_uri)

        token_data = platform.auth().data()
        token_store[state] = token_data
        logger.info(f"🗓️ Storing token for session: {state}")
        save_tokens()

        expiry = token_data.get('expireTime', 'unknown')
        logger.info(f"✅ OAuth login successful for session: {state} | Token expires at: {expiry}")

        return PlainTextResponse("OAuth login successful", status_code=200)

    except Exception as e:
        logger.error(f"❌ OAuth callback failed\n{e}", exc_info=True)
        raise HTTPException(status_code=400, detail="OAuth callback failed")

@app.post("/api/discover-meeting-rooms")
async def discover_meeting_rooms(data: MeetingRoomDiscoveryRequest):
    logs = []
    try:
        cache_key = f"{data.sessionId}-{data.startDate}-{data.endDate}-{'-'.join(data.userIds)}"
        cached = meeting_room_cache.get(cache_key)
        if cached and datetime.now(timezone.utc) < cached["expires_at"]:
            logs.append("♻️ Using cached results")
            return {"rooms": cached["rooms"], "logs": logs}

        platform = get_platform(data.sessionId, logs)
        all_groups = platform.get(f"/restapi/v1.0/glip/groups?recordCount=100").json_dict().get("records", [])
        logs.append(f"🏡 Retrieved {len(all_groups)} groups")

        rooms = {}
        for group in all_groups:
            if group.get("isArchived") or group.get("type") != "Team":
                continue

            group_id = group.get("id")
            group_name = group.get("name") or group_id
            url = f"/restapi/v1.0/glip/groups/{group_id}/posts?recordCount=100&dateFrom={data.startDate}T00:00:00Z&dateTo={data.endDate}T23:59:59Z"
            try:
                result = await ringcentral_get_with_retry(platform, url, logs)
                posts = result.json_dict().get("records", [])
                if any(post.get("creatorId") in data.userIds for post in posts):
                    rooms[group_id] = group_name
                    logs.append(f"✅ Found posts in {group_name}")
            except Exception as e:
                logs.append(f"❌ Failed to check posts in {group_name}: {e}")

        meeting_room_cache[cache_key] = {
            "rooms": rooms,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=CACHE_TTL_SECONDS)
        }

        return {"rooms": rooms, "logs": logs}

    except Exception as e:
        logs.append(f"❗ Unexpected error during meeting room discovery: {e}")
        return JSONResponse(status_code=500, content={"error": "Internal server error", "logs": logs})
