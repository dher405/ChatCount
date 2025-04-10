from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from ringcentral import SDK
from datetime import datetime, timezone
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

token_store = load_tokens()

# In-memory cache for meeting room discovery
meeting_room_cache: Dict[str, Dict[str, str]] = {}

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
    MAX_RETRIES = 5
    try:
        cache_key = f"{data.sessionId}:{data.startDate}:{data.endDate}:{'-'.join(data.userIds)}"
        if cache_key in meeting_room_cache:
            logs.append("♻️ Using cached results")
            return {"rooms": meeting_room_cache[cache_key], "logs": logs}

        if data.sessionId not in token_store:
            return JSONResponse(status_code=401, content={"error": "Not authenticated with RingCentral"})

        rcsdk = SDK(client_id, client_secret, server_url, redirect_uri)
        platform = rcsdk.platform()
        platform.auth().set_data(token_store[data.sessionId])

        start_date = datetime.fromisoformat(data.startDate).replace(tzinfo=timezone.utc)
        end_date = datetime.fromisoformat(data.endDate).replace(tzinfo=timezone.utc)

        room_posts = {}
        user_names = {}

        for user_id in data.userIds:
            try:
                user_info = platform.get(f"/restapi/v1.0/account/~/extension/{user_id}").json_dict()
                user_names[user_id] = user_info.get("name") or user_id
            except Exception:
                user_names[user_id] = user_id

        all_groups = platform.get("/restapi/v1.0/glip/groups?recordCount=100").json_dict().get("records", [])
        logs.append(f"🏨 Retrieved {len(all_groups)} active team groups for inspection")

        for group in all_groups:
            group_id = group.get("id")
            group_name = group.get("name") or group_id
            if not group_id or group.get("isArchived") or group.get("type") != "Team":
                continue

            retries = 0
            while retries < MAX_RETRIES:
                try:
                    response = platform.get(
                        f"/restapi/v1.0/glip/groups/{group_id}/posts",
                        {
                            "recordCount": 100,
                            "dateFrom": start_date.isoformat(),
                            "dateTo": end_date.isoformat()
                        }
                    )
                    posts = response.json_dict().get("records", [])
                    if any(post.get("creatorId") in data.userIds for post in posts):
                        logs.append(f"✅ Found posts in {group_name}")
                        room_posts[group_id] = group_name
                    break
                except Exception as e:
                    if "CMN-301" in str(e):
                        delay = 2 ** retries
                        logs.append(f"⏳ Rate limit hit. Retrying {group_name} in {delay}s...")
                        await asyncio.sleep(delay)
                        retries += 1
                    else:
                        logs.append(f"❌ Error fetching posts for {group_name}: {e}")
                        break

        meeting_room_cache[cache_key] = room_posts
        return {"rooms": room_posts, "logs": logs}

    except Exception as e:
        logs.append(f"❗ Unexpected error during meeting room discovery: {e}")
        return JSONResponse(status_code=500, content={"error": "Internal server error", "logs": logs})

