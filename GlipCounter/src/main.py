\from fastapi import FastAPI, Request, HTTPException
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

def refresh_session_token(session_id):
    token_data = token_store.get(session_id)
    if not token_data:
        raise HTTPException(status_code=401, detail="Session not found")
    rcsdk = SDK(client_id, client_secret, server_url, redirect_uri)
    platform = rcsdk.platform()
    platform.auth().set_data(token_data)
    try:
        platform.refresh()
        token_store[session_id] = platform.auth().data()
        save_tokens()
        logger.info(f"🔄 Refreshed token for session {session_id}")
    except Exception as e:
        logger.warning(f"⚠️ Token refresh skipped or failed for session {session_id}: {e}")
    return platform

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
        platform = refresh_session_token(data.sessionId)

        cache_key = f"{data.sessionId}-{data.startDate}-{data.endDate}-{'-'.join(data.userIds)}"
        cache_entry = meeting_room_cache.get(cache_key)
        if cache_entry and cache_entry['expiry'] > time.time():
            logs.append("♻️ Using cached results")
            return {"rooms": cache_entry['data'], "logs": logs}
        elif cache_entry:
            del meeting_room_cache[cache_key]  # Cleanup expired

        start_date = datetime.fromisoformat(data.startDate).replace(tzinfo=timezone.utc)
        end_date = datetime.fromisoformat(data.endDate).replace(tzinfo=timezone.utc)

        room_posts = {}
        all_groups = platform.get(f"/restapi/v1.0/glip/groups?recordCount=100").json().get("records", [])

        logs.append(f"🏘️ Retrieved {len(all_groups)} active team groups for inspection")

        for group in all_groups:
            group_id = group.get("id")
            group_name = group.get("name")
            if not group_id or group.get("isArchived") or group.get("type") != "Team":
                continue

            try:
                posts = platform.get(
                    f"/restapi/v1.0/glip/groups/{group_id}/posts?recordCount=100&dateFrom={start_date.isoformat()}&dateTo={end_date.isoformat()}"
                ).json().get("records", [])

                if any(post.get("creatorId") in data.userIds for post in posts):
                    room_posts[group_id] = group_name or group_id
                    logs.append(f"✅ Found user activity in group {group_id}: {group_name}")
            except Exception as e:
                logs.append(f"⚠️ Error checking group {group_id}: {e}")

        meeting_room_cache[cache_key] = {
            "data": room_posts,
            "expiry": time.time() + CACHE_TTL_SECONDS
        }
        return {"rooms": room_posts, "logs": logs}

    except Exception as e:
        logs.append(f"❗ Unexpected error during meeting room discovery: {e}")
        return JSONResponse(status_code=500, content={"error": "Internal server error", "logs": logs})
