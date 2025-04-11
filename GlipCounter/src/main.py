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
        expires_at = token_data.get("expireTime")
        if expires_at and datetime.utcnow().timestamp() > expires_at / 1000:
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
            response = platform.get(url)
            if not response:
                logs.append(f"⚠️ Null response received for {url}")
                return None
            headers = response.response().headers
            if int(headers.get("X-Rate-Limit-Remaining", 1)) <= 1:
                wait = int(headers.get("X-Rate-Limit-Window", 60))
                logs.append(f"🛑 Approaching rate limit. Sleeping for {wait}s.")
                await asyncio.sleep(wait)
            return response
        except Exception as e:
            if "CMN-301" in str(e):
                logs.append(f"⏳ Error occurred. Retrying in {wait_time} seconds... ({e})")
                await asyncio.sleep(wait_time)
                wait_time *= 2
            elif "403" in str(e):
                logs.append(f"🛘 Skipping inaccessible group (403): {e}")
                return None
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

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/")
def root():
    return {"message": "GlipCounter API is up and running."}

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

...
...

class TrackPostsRequest(BaseModel):
    startDate: str
    endDate: str
    userIds: List[str]
    roomIds: List[str]
    sessionId: str

@app.post("/api/discover-meeting-rooms")
async def discover_meeting_rooms(data: MeetingRoomDiscoveryRequest):
    logs = []
    try:
        platform = get_platform(data.sessionId, logs)
        cache_key = f"{data.sessionId}-{','.join(data.userIds)}-{data.startDate}-{data.endDate}"
        if cache_key in meeting_room_cache:
            cached = meeting_room_cache[cache_key]
            if time.time() - cached['timestamp'] < CACHE_TTL_SECONDS:
                logs.append("♻️ Returning cached meeting room data")
                return {"rooms": cached['rooms'], "logs": logs}

        start_date = datetime.fromisoformat(data.startDate).replace(tzinfo=timezone.utc)
        end_date = datetime.fromisoformat(data.endDate).replace(tzinfo=timezone.utc) + timedelta(days=1) - timedelta(seconds=1)

        response = await ringcentral_get_with_retry(platform, f"/restapi/v1.0/glip/groups?recordCount=100", logs)
        all_groups = response.json_dict().get("records", [])

        rooms = {}
        for group in all_groups:
            group_id = group.get("id")
            group_name = group.get("name")
            if group.get("type") != "Team" or group.get("isArchived"):
                continue

            next_page = None
            matching_post_ids = []

            while True:
                post_url = f"/restapi/v1.0/glip/groups/{group_id}/posts?recordCount=100&dateFrom={start_date.isoformat()}&dateTo={end_date.isoformat()}"
                if next_page:
                    post_url += f"&pageToken={next_page}"

                posts_resp = await ringcentral_get_with_retry(platform, post_url, logs)
                if posts_resp is None:
                    break

                result = posts_resp.json_dict()
                posts = result.get("records", [])

                for post in posts:
                    post_id = post.get("id")
                    creator_id = post.get("creatorId")
                    creation_time = post.get("creationTime")

                    if not creator_id or not creation_time:
                        continue

                    post_time = datetime.fromisoformat(creation_time.replace("Z", "+00:00"))
                    if start_date <= post_time <= end_date and creator_id in data.userIds:
                        matching_post_ids.append(post_id)

                next_link = result.get("navigation", {}).get("nextPage", {}).get("uri")
                if not next_link:
                    break
                next_page = next_link.split("pageToken=")[-1]

            if matching_post_ids:
                rooms[group_id] = group_name or group_id
                logs.append(f"✅ {len(matching_post_ids)} matching post(s) in room {group_name or group_id} → Post IDs: {matching_post_ids}")
            else:
                logs.append(f"🚫 No matching posts in room {group_name or group_id} within date range.")

        meeting_room_cache[cache_key] = {"rooms": rooms, "timestamp": time.time()}
        return {"rooms": rooms, "logs": logs}

    except Exception as e:
        logs.append(f"❗ Unexpected error during meeting room discovery: {e}")
        return JSONResponse(status_code=500, content={"error": "Internal server error", "logs": logs})


@app.post("/api/track-posts")
async def track_posts(data: TrackPostsRequest):
    logs = []
    results = {}
    try:
        platform = get_platform(data.sessionId, logs)
        start_date = datetime.fromisoformat(data.startDate).replace(tzinfo=timezone.utc)
        end_date = datetime.fromisoformat(data.endDate).replace(tzinfo=timezone.utc) + timedelta(days=1) - timedelta(seconds=1)

        for room_id in data.roomIds:
            next_page = None
            post_counts = {}
            matching_post_ids = []

            while True:
                post_url = f"/restapi/v1.0/glip/groups/{room_id}/posts?recordCount=100&dateFrom={start_date.isoformat()}&dateTo={end_date.isoformat()}"
                if next_page:
                    post_url += f"&pageToken={next_page}"

                posts_resp = await ringcentral_get_with_retry(platform, post_url, logs)
                if posts_resp is None:
                    break

                result = posts_resp.json_dict()
                posts = result.get("records", [])

                for post in posts:
                    creator_id = post.get("creatorId")
                    post_id = post.get("id")
                    creation_time = post.get("creationTime")

                    if not creator_id or not creation_time:
                        continue

                    post_time = datetime.fromisoformat(creation_time.replace("Z", "+00:00"))
                    if start_date <= post_time <= end_date and creator_id in data.userIds:
                        post_counts[creator_id] = post_counts.get(creator_id, 0) + 1
                        matching_post_ids.append(post_id)

                next_link = result.get("navigation", {}).get("nextPage", {}).get("uri")
                if not next_link:
                    break
                next_page = next_link.split("pageToken=")[-1]

            results[room_id] = post_counts
            logs.append(f"📊 Room {room_id}: {post_counts} → Post IDs: {matching_post_ids}")

        return {"results": results, "logs": logs}

    except Exception as e:
        logs.append(f"❗ Unexpected error during post tracking: {e}")
        return JSONResponse(status_code=500, content={"error": "Internal server error", "logs": logs})



