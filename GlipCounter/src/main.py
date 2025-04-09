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
    allow_origins=["http://localhost:5173"],
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

@app.get("/")
def root():
    return {"message": "Welcome to GlipCounter API"}

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
            return JSONResponse(status_code=401, content={"error": "Not authenticated with RingCentral"})

        rcsdk = SDK(client_id, client_secret, server_url, redirect_uri)
        platform = rcsdk.platform()
        platform.auth().set_data(token_store[data.sessionId])

        start_date = datetime.fromisoformat(data.startDate).replace(tzinfo=timezone.utc)
        end_date = datetime.fromisoformat(data.endDate).replace(tzinfo=timezone.utc)

        room_posts = {}
        cache_key = f"{data.sessionId}-{data.startDate}-{data.endDate}-{'-'.join(data.userIds)}"
        if cache_key in group_cache:
            logs.append("♻️ Using cached results")
            return {"rooms": group_cache[cache_key], "logs": logs}

        all_groups = []
        group_page_token = None

        while True:
            group_params = {"recordCount": 100}
            if group_page_token:
                group_params["pageToken"] = group_page_token

            group_response = platform.get("/restapi/v1.0/glip/groups", group_params)
            group_result = group_response.json_dict()
            all_groups.extend(group_result.get("records", []))

            next_group_page = group_result.get("navigation", {}).get("nextPage", {}).get("uri")
            if next_group_page:
                group_page_token = next_group_page.split("pageToken=")[-1]
            else:
                break

        filtered_groups = [g for g in all_groups if not g.get("isArchived") and g.get("type") == "Team"]
        logs.append(f"🏘️ Retrieved {len(filtered_groups)} active team groups for inspection")

        async def fetch_posts_for_group(group, retries=3):
            group_id = group.get("id")
            group_name = group.get("name") or f"Group-{group_id}"
            if not group_id:
                return None

            post_params = {
                "recordCount": 100,
                "dateFrom": start_date.isoformat(),
                "dateTo": end_date.isoformat()
            }

            next_page = None
            while True:
                if next_page:
                    post_params["pageToken"] = next_page
                try:
                    post_response = platform.get(f"/restapi/v1.0/glip/groups/{group_id}/posts", post_params)
                    headers = post_response.response().headers
                    remaining = int(headers.get("X-Rate-Limit-Remaining", "1"))
                    window = int(headers.get("X-Rate-Limit-Window", "60"))

                    if remaining <= 1:
                        logs.append(f"🛑 Rate limit nearly exceeded. Waiting {window}s before continuing...")
                        await asyncio.sleep(window)

                    post_result = post_response.json_dict()
                    posts = post_result.get("records", [])
                    if any(p.get("creatorId") in data.userIds for p in posts):
                        logs.append(f"✅ Match found in group {group_id}: {group_name}")
                        return group_id, group_name

                    next_link = post_result.get("navigation", {}).get("nextPage", {}).get("uri")
                    if next_link:
                        next_page = next_link.split("pageToken=")[-1]
                    else:
                        break
                except Exception as e:
                    if "CMN-301" in str(e) and retries > 0:
                        wait_time = 2 ** (3 - retries)
                        logs.append(f"⏳ Rate limit hit. Retrying {group_id} in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        retries -= 1
                        continue
                    else:
                        logs.append(f"⚠️ Error fetching posts for group {group_id}: {e}")
                        break
            logs.append(f"🚫 No matching posts found in group {group_id}: {group_name}")
            return None

        batch_size = 3
        for i in range(0, len(filtered_groups), batch_size):
            batch = filtered_groups[i:i + batch_size]
            results = await asyncio.gather(*[fetch_posts_for_group(group) for group in batch])
            for result in results:
                if result:
                    group_id, group_name = result
                    room_posts[group_id] = group_name
            await asyncio.sleep(2)

        sorted_rooms = dict(sorted(room_posts.items()))
        group_cache[cache_key] = sorted_rooms
        return {"rooms": sorted_rooms, "logs": logs}

    except Exception as e:
        logs.append(f"❗ Unexpected error during meeting room discovery: {e}")
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
            while True:
                if next_page:
                    post_params["pageToken"] = next_page
                response = platform.get(f"/restapi/v1.0/glip/groups/{group_id}/posts", post_params)
                result = response.json_dict()
                posts = result.get("records", [])
                for post in posts:
                    uid = post.get("creatorId")
                    uname = user_names.get(uid)
                    if uname in user_post_map:
                        user_post_map[uname] += 1
                next_link = result.get("navigation", {}).get("nextPage", {}).get("uri")
                if next_link:
                    next_page = next_link.split("pageToken=")[-1]
                else:
                    break
            post_counts[group_name] = user_post_map

        return {"posts": post_counts, "logs": logs}
    except Exception as e:
        logs.append(f"❗ Error during post tracking: {e}")
        return JSONResponse(status_code=500, content={"error": "Internal server error", "logs": logs})
