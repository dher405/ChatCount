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
import traceback

# Load environment variables from cred.env file
load_dotenv(dotenv_path='cred.env')

app = FastAPI()

# CORS config for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",         # for local dev
        "https://chatcount-fe.onrender.com"  # deployed frontend
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

# In-memory group post discovery cache
group_cache: Dict[str, Dict[str, str]] = {}

@app.post("/api/discover-meeting-rooms")
async def discover_meeting_rooms(data: MeetingRoomDiscoveryRequest):
    logs = []
    try:
        if data.sessionId not in token_store:
            logger.warning(f"🔒 No token found for session {data.sessionId}")
            return JSONResponse(status_code=401, content={"error": "Not authenticated with RingCentral"})

        rcsdk = SDK(client_id, client_secret, server_url, redirect_uri)
        platform = rcsdk.platform()
        platform.auth().set_data(token_store[data.sessionId])

        start_date = datetime.fromisoformat(data.startDate).replace(tzinfo=timezone.utc)
        end_date = datetime.fromisoformat(data.endDate).replace(tzinfo=timezone.utc)

        room_posts = {}
        all_groups = platform.get("/restapi/v1.0/glip/groups?recordCount=100").json_dict().get("records", [])

        logs.append(f"🏨 Retrieved {len(all_groups)} active team groups for inspection")

        for group in all_groups:
            group_id = group.get("id")
            group_name = group.get("name") or f"Group-{group_id}"
            if not group_id or group.get("isArchived") or group.get("type") != "Team":
                continue

            try:
                posts = platform.get(
                    f"/restapi/v1.0/glip/groups/{group_id}/posts?recordCount=100&dateFrom={start_date.isoformat()}&dateTo={end_date.isoformat()}"
                ).json_dict().get("records", [])

                if any(post.get("creatorId") in data.userIds for post in posts):
                    room_posts[group_id] = group_name
                    logs.append(f"✅ Found user activity in group {group_id}: {group_name}")
            except Exception as e:
                logs.append(f"⚠️ Error checking group {group_id}: {e}")

        return {"rooms": room_posts, "logs": logs}

    except Exception as e:
        logs.append(f"❗ Unexpected error during meeting room discovery: {e}")
        logs.append(traceback.format_exc())
        return JSONResponse(status_code=500, content={"error": "Internal server error", "logs": logs})

@app.post("/api/track-posts")
async def track_posts(data: TrackPostsRequest):
    logs = []
    try:
        if data.sessionId not in token_store:
            return JSONResponse(status_code=401, content={"error": "Not authenticated with RingCentral"})

        rcsdk = SDK(client_id, client_secret, server_url, redirect_uri)
        platform = rcsdk.platform()
        platform.auth().set_data(token_store[data.sessionId])

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
            post_params = {
                "recordCount": 100,
                "dateFrom": start_date.isoformat(),
                "dateTo": end_date.isoformat()
            }
            next_page = None
            retries = 3
            while True:
                try:
                    if next_page:
                        post_params["pageToken"] = next_page
                    response = platform.get(f"/restapi/v1.0/glip/groups/{group_id}/posts", post_params)
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
                    if hasattr(e, 'response') and e.response.status_code == 429:
                        retry_after = int(e.response.headers.get("Retry-After", "30"))
                        logs.append(f"🛑 Rate limit hit for group {group_id}. Retrying in {retry_after} seconds...")
                        await asyncio.sleep(retry_after)
                        continue
                    elif retries > 0:
                        wait_time = 2 ** (3 - retries)
                        logs.append(f"⏳ Error occurred. Retrying in {wait_time} seconds... ({e})")
                        await asyncio.sleep(wait_time)
                        retries -= 1
                        continue
                    else:
                        logs.append(f"❌ Failed to retrieve posts for group {group_id} after retries: {e}")
                        break
            post_counts[group_name] = user_post_map

        return {"posts": post_counts, "logs": logs}
    except Exception as e:
        logs.append(f"❗ Error during post tracking: {e}")
        logs.append(traceback.format_exc())
        return JSONResponse(status_code=500, content={"error": "Internal server error", "logs": logs})
