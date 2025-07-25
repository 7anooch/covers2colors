import pylast
import requests
import json
import os
import pkg_resources
import musicbrainzngs
import discogs_client
import time
from fuzzywuzzy import fuzz

api_key = None
discogs_token = None


def load_api_keys():
    """Load API keys from ``keys.json`` or environment variables."""
    global api_key, discogs_token
    if api_key is not None or discogs_token is not None:
        return api_key, discogs_token

    keys_path = pkg_resources.resource_filename("covers2colors", "keys.json")
    if os.path.exists(keys_path):
        with open(keys_path, "r") as f:
            config = json.load(f)
            api_key = config.get("lastfm", {}).get("api_key")
            discogs_token = config.get("discogs", {}).get("token")
    else:
        api_key = os.environ.get("LASTFM_API_KEY")
        discogs_token = os.environ.get("DISCOGS_TOKEN")

    return api_key, discogs_token

USER_AGENT = "covers2colors"
USER_AGENT_VERSION = "0.1"
USER_AGENT_URL = "http://idonthaveawebsite.com"
COVER_ART_URL_TEMPLATE = "https://coverartarchive.org/release/{}/front-500"

def get_lastfm_cover_art_url(api_key, artist_name, album_name, max_retries=3):
    """ Fetches the album cover art URL from the Last.fm API for a given artist and album."""
    network = pylast.LastFMNetwork(api_key=api_key)
    album = network.get_album(artist_name, album_name)

    for i in range(max_retries):
        try:
            cover_art_url = album.get_cover_image()
            if cover_art_url:
                return cover_art_url
        except Exception as e:
            print(f"Error fetching cover art for {album_name}: {e}")
            time.sleep(2)  # Wait for 2 seconds before retrying

    return None


def check_list_in_result(result, key, name):
    if not (key in result and result[key]):
        print(f"{key.replace('-', ' ').capitalize()} not found for {name}")
        return False
    return True

def get_mb_cover_art_url(artist_name, album_name):
    """ Get cover art URL using MusicBrainz data and artist and album names """
    musicbrainzngs.set_useragent(USER_AGENT, USER_AGENT_VERSION, USER_AGENT_URL)
    # artist_name = artist_name.lower()
    # album_name = album_name.lower()
    name = f"{artist_name} - {album_name}"

    try:
        # Search for release groups
        release_group_result = musicbrainzngs.search_release_groups(artist=artist_name, release=album_name, limit=5)

        # Use fuzzy string matching to find the best match
        best_match = None
        highest_ratio = 0
        match_threshold = 80
        for release_group in release_group_result['release-group-list']:
            ratio = fuzz.ratio(name.lower(), f"{release_group['artist-credit'][0]['artist']['name']} - {release_group['title']}".lower())
            if ratio > highest_ratio and ratio >= match_threshold:
                highest_ratio = ratio
                best_match = release_group

        if best_match is None:
            print(f"Release group not found for {name}")
            return None

        release_group_id = best_match['id']

        releases_result = musicbrainzngs.browse_releases(release_group=release_group_id, limit=1)

        if not check_list_in_result(releases_result, 'release-list', name):
            return None

        release = releases_result['release-list'][0]
        release_id = release['id']
        cover_art_url = COVER_ART_URL_TEMPLATE.format(release_id)

        #Check if cover art exists
        with requests.get(cover_art_url, stream=True) as response:
            if response.status_code != 200:
                print(f"Cover art not found for {name}")
                return None

        return cover_art_url

    except musicbrainzngs.MusicBrainzError as e:
        print(f"Error fetching cover art for {name}: {e}")
    except requests.exceptions.RequestException as e:
        print(f"Error checking cover art existence for {name}: {e}")

    return None

def get_discogs_cover_art_url(artist_name, album_name, user_token):
    """ Fetches the album cover art URL from the Discogs API for a given artist and album."""
    d = discogs_client.Client("covers2colors/0.1", user_token=user_token)
    try:
        discogs_search = d.search(artist=artist_name, release_title=album_name, type="release")
        results = discogs_search.page(1)

        if results:
            best_match = results[0]
            cover_art_url = None
            if best_match.images:
                cover_art_url = best_match.images[0].get("uri")
            if cover_art_url:
                return cover_art_url
            print(f"Cover art not found for {artist_name} - {album_name}")
        else:
            print(f"Release not found for {artist_name} - {album_name}")

    except discogs_client.exceptions.HTTPError as e:
        print(f"Error fetching cover art from Discogs for {artist_name} - {album_name}: {e}")

    return None

def get_best_cover_art_url(artist_name, album_name, api_key=None, user_token=None):
    """Fetch the album cover art URL using the best available method."""
    if api_key is None or user_token is None:
        loaded_api_key, loaded_discogs = load_api_keys()
        if api_key is None:
            api_key = loaded_api_key
        if user_token is None:
            user_token = loaded_discogs

    cover_art_url = None

    if api_key:
        # Try Last.fm first if API key is provided
        print("Attempting to get cover art from last.fm")
        cover_art_url = get_lastfm_cover_art_url(api_key, artist_name, album_name)
    
    if not cover_art_url:
        # Try MusicBrainz if no cover art was found or if no Last.fm API key was provided
        print("Attempting to get cover art from MusicBrainz")
        cover_art_url = get_mb_cover_art_url(artist_name, album_name)

    if not cover_art_url and user_token != None:
        # Try Discogs if no cover art was found in previous methods if discog token is provided
        print("Attempting to get cover art from Discogs")
        cover_art_url = get_discogs_cover_art_url(artist_name, album_name, user_token)

    return cover_art_url
