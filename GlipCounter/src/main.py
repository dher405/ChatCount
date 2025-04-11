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

def refresh_session_token(session_id):
    token_data = token_store.get(session_id)
    if not token_data:
        raise HTTPException(status_code=401, detail="Session not found")
    rcsdk = SDK(client_id, client_secret, server_url, redirect_uri)
    platform = rcsdk.platform()
    platform.auth().set_data(token_data)
    try:
        if platform.logged_in():
            platform.refresh()
            token_store[session_id] = platform.auth().data()
            save_tokens()
            logger.info(f"🔄 Refreshed token for session {session_id}")
    except Exception as e:
        logger.warning(f"⚠️ Token refresh skipped or failed for session {session_id}: {e}")
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
                raise
    logs.append(f"❌ Failed to retrieve data after retries: {e}")
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
        all_groups_response = await ringcentral_get_with_retry(platform, f"/restapi/v1.0/glip/groups?recordCount=100", logs)
        all_groups = all_groups_response.json_dict().get("records", [])

        logs.append(f"🏘️ Retrieved {len(all_groups)} active team groups for inspection")

        for group in all_groups:
            group_id = group.get("id")
            group_name = group.get("name")
            if not group_id or group.get("isArchived") or group.get("type") != "Team":
                continue

            try:
                posts_response = await ringcentral_get_with_retry(
                    platform,
                    f"/restapi/v1.0/glip/groups/{group_id}/posts?recordCount=100&dateFrom={start_date.isoformat()}&dateTo={end_date.isoformat()}",
                    logs
                )
                posts = posts_response.json_dict().get("records", [])

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


@app.post("/api/track-posts")
async def track_posts(data: TrackPostsRequest):
    logs = []
    try:
        platform = refresh_session_token(data.sessionId)

        cache_key = f"{data.sessionId}-{data.startDate}-{data.endDate}-{'-'.join(data.userIds)}-{'-'.join(data.meetingRooms)}"
        cache_entry = post_tracking_cache.get(cache_key)
        if cache_entry and cache_entry['expiry'] > time.time():
            logs.append("♻️ Using cached results")
            return {"posts": cache_entry['data'], "logs": logs}
        elif cache_entry:
            del post_tracking_cache[cache_key]  # Cleanup expired

        start_date = datetime.fromisoformat(data.startDate).replace(tzinfo=timezone.utc)
        end_date = datetime.fromisoformat(data.endDate).replace(tzinfo=timezone.utc)

        post_counts = {}
        user_names = {}
        room_names = {}

        for user_id in data.userIds:
            try:
                user_info = platform.get(f"/restapi/v1.0/account/~/extension/{user_id}").json_dict()
                user_names[user_id] = user_info.get("name") or user_id
            except Exception:
                user_names[user_id] = user_id

        for group_id in data.meetingRooms:
            try:
                group_info = platform.get(f"/restapi/v1.0/glip/groups/{group_id}").json_dict()
                room_names[group_id] = group_info.get("name") or group_id
            except Exception:
                room_names[group_id] = group_id

        for group_id in data.meetingRooms:
            group_name = room_names.get(group_id, group_id)
            user_post_map = {user_names.get(user_id, user_id): 0 for user_id in data.userIds}
            post_params = f"recordCount=100&dateFrom={start_date.isoformat()}&dateTo={end_date.isoformat()}"
            next_page = ""

            while True:
                try:
                    url = f"/restapi/v1.0/glip/groups/{group_id}/posts?{post_params}"
                    if next_page:
                        url += f"&pageToken={next_page}"

                    response = await ringcentral_get_with_retry(platform, url, logs)
                    result = response.json_dict()
                    posts = result.get("records", [])
                    for post in posts:
                        post_time = post.get("creationTime")
                        uid = post.get("creatorId")
                        uname = user_names.get(uid)
                        if post_time:
                            try:
                                post_dt = datetime.fromisoformat(post_time.replace("Z", "+00:00"))
                                if start_date <= post_dt <= end_date:
                                    if uname in user_post_map:
                                        user_post_map[uname] += 1
                                        logs.append(f"🧞 Counted post by {uname} in {group_name} at {post_dt.isoformat()}")
                                else:
                                    logs.append(f"⏳ Skipped post by {uname} at {post_dt.isoformat()} (outside date range)")
                            except Exception as e:
                                logs.append(f"❌ Failed to parse post timestamp: {post_time} ({e})")
                    next_link = result.get("navigation", {}).get("nextPage", {}).get("uri")
                    if next_link:
                        next_page = next_link.split("pageToken=")[-1]
                    else:
                        break
                except Exception as e:
                    logs.append(f"❌ Error tracking posts in group {group_id}: {e}")
                    break

            post_counts[group_name] = user_post_map

        post_tracking_cache[cache_key] = {
            "data": post_counts,
            "expiry": time.time() + CACHE_TTL_SECONDS
        }

        return {"posts": post_counts, "logs": logs}

    except Exception as e:
        logs.append(f"❗ Error during post tracking: {e}")
        return JSONResponse(status_code=500, content={"error": "Internal server error", "logs": logs})

