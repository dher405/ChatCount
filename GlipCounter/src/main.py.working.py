from fastapi import FastAPI, Request, HTTPException, Response, Depends
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from ringcentral import SDK
from datetime import datetime
import os
import logging
from uuid import uuid4
from dotenv import load_dotenv
from typing import List
import urllib.parse

# Load environment variables from cred.env file
load_dotenv(dotenv_path='cred.env')

app = FastAPI()

# CORS config for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load credentials from environment variables
client_id = os.getenv("RC_CLIENT_ID")
client_secret = os.getenv("RC_CLIENT_SECRET")
server_url = os.getenv("RC_SERVER_URL", "https://platform.ringcentral.com")
redirect_uri = os.getenv("RC_REDIRECT_URI")

# Validate required config
if not all([client_id, client_secret, redirect_uri]):
    raise EnvironmentError("Missing one or more RingCentral configuration values in .env")

# Temporary in-memory token store (use a real DB or secure store in production)
token_store = {}

class TrackPostsRequest(BaseModel):
    startDate: str
    endDate: str
    meetingRooms: List[str]
    userId: str
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
        rcsdk = SDK(client_id, client_secret, server_url, redirect_uri)
        platform = rcsdk.platform()
        platform.login(code=code)
        token_store[state] = platform.auth().data()
        logger.info(f"OAuth login successful for session: {state}")
        return RedirectResponse(url=f"http://localhost:3000/oauth-success?sessionId={state}")
    except Exception as e:
        logger.error(f"OAuth callback failed: {str(e)}")
        raise HTTPException(status_code=400, detail="OAuth callback failed")

@app.post("/api/track-posts")
async def track_posts(data: TrackPostsRequest):
    try:
        if data.sessionId not in token_store:
            raise HTTPException(status_code=401, detail="Not authenticated with RingCentral")

        rcsdk = SDK(client_id, client_secret, server_url, redirect_uri)
        platform = rcsdk.platform()
        platform.auth().set_data(token_store[data.sessionId])

        # Refresh token if needed
        if platform.auth().access_token_expired():
            platform.refresh()
            token_store[data.sessionId] = platform.auth().data()
            logger.info(f"Token refreshed for session: {data.sessionId}")

        start_date = datetime.fromisoformat(data.startDate)
        end_date = datetime.fromisoformat(data.endDate)

        post_counts = {}

        for team_id in data.meetingRooms:
            post_counts[team_id] = 0
            page_token = None

            while True:
                params = {'recordCount': 100}
                if page_token:
                    params['pageToken'] = page_token

                endpoint = f'/restapi/v1.0/glip/groups/{team_id}/posts'
                response = platform.get(endpoint, params)
                res_data = response.json()

                for post in res_data.get('records', []):
                    post_time = datetime.fromisoformat(post['creationTime'].replace('Z', '+00:00'))
                    if data.userId == post['creatorId'] and start_date <= post_time <= end_date:
                        post_counts[team_id] += 1

                next_page = res_data.get('navigation', {}).get('nextPage', {}).get('uri')
                if next_page:
                    page_token = next_page.split('pageToken=')[-1]
                else:
                    break

        logger.info(f"Post count retrieval successful for session {data.sessionId}: {post_counts}")
        return post_counts
    except HTTPException as he:
        logger.warning(f"HTTP error during post tracking: {he.detail}")
        raise he
    except Exception as e:
        logger.error(f"Error during post tracking: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
