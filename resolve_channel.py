import requests

API_KEY = "AIzaSyBs6xV-t34Rs8Vpa-toQtqT__IXT_YLGLM"

handle = "@OuroKronii"

url = "https://www.googleapis.com/youtube/v3/search"

params = {
    "part": "snippet",
    "q": handle,
    "type": "channel",
    "maxResults": 1,
    "key": API_KEY
}

response = requests.get(url, params=params)
data = response.json()

if "items" in data and len(data["items"]) > 0:
    channel_id = data["items"][0]["id"]["channelId"]
    channel_title = data["items"][0]["snippet"]["title"]

    print("Channel Title:", channel_title)
    print("Channel ID:", channel_id)
else:
    print("Channel not found.")
